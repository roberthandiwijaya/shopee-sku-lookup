from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services import catalog_pdf_service, catalog_export_service as export_jobs, product_service
from app.config import settings
from app.services import catalog_identity, catalog_stock_auth

router = APIRouter(tags=["public"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters["idr"] = lambda value: "-" if value is None else f"{float(value):,.0f}".replace(",", ".")

LINKED_PRODUCTS_PER_PAGE = 10
ALLOWED_LINKED_PRODUCTS_PER_PAGE = {10, 20, 50}


class CatalogExportRequest(BaseModel):
    family_codes: list[str] = Field(min_length=1, max_length=catalog_pdf_service.MAX_FAMILIES)
    stock_mode: Literal["all", "available"] = "available"
    min_stock: int = Field(default=1, ge=1, le=1_000_000)
    show_stock: bool = False
    show_prices: bool = True


class BackgroundExportRequest(CatalogExportRequest):
    family_codes: list[str] = Field(min_length=1, max_length=250)
    output_mode: Literal["single", "split"] = "single"


@router.get("/linked-products/export-session")
async def export_session(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": catalog_identity.bootstrap(request),
            "can_export_quantities": await catalog_stock_auth.can_export_quantities(request, session)}


@router.post("/linked-products/exports", status_code=202)
async def create_catalog_export(payload: BackgroundExportRequest, request: Request, response: Response,
                                session: AsyncSession = Depends(get_session)):
    owner = catalog_identity.require_export_session(request)
    quantity_user = await catalog_stock_auth.require_quantity_permission(request, session, payload.show_stock)
    network = catalog_identity.network_hash(request)
    options = payload.model_dump()
    if quantity_user is not None:
        options['quantity_admin_user_id'] = quantity_user
    try:
        job, token = await export_jobs.create_job(session, options, owner, network)
    except export_jobs.RateLimited as exc:
        return catalog_identity.limited_response(exc)
    except export_jobs.QueueFull as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "30", "Cache-Control": "no-store"}) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"id": job.id, "token": token, "status": job.status}


async def _get_export_job(request, session, job_id, token=None):
    if token is None:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    job = await export_jobs.authorized_job(session, job_id, token)
    if not job:
        raise HTTPException(404, "Export not found.")
    expires = job.expires_at
    if expires.tzinfo is None:
        from app.models.product import TZ_GMT7
        expires = expires.replace(tzinfo=TZ_GMT7)
    if expires <= export_jobs.now() or job.status == "expired":
        raise HTTPException(410, "Export expired. Generate a new catalog.")
    return job


@router.get("/linked-products/exports/{job_id}")
async def catalog_export_status(job_id: str, request: Request, response: Response,
                                session: AsyncSession = Depends(get_session)):
    job = await _get_export_job(request, session, job_id)
    response.headers["Cache-Control"] = "no-store"
    return {"id": job.id, "status": job.status, "progress": job.progress, "total": job.total,
            "error": job.error, "warnings": job.warnings or [], "expires_at": job.expires_at,
            "snapshot_at": job.snapshot_at, "cancel_requested": job.cancel_requested,
            "files": (job.result or {}).get("files", []),
            "omitted": (job.result or {}).get("omitted", 0)}


@router.post("/linked-products/exports/{job_id}/cancel")
async def cancel_catalog_export(job_id: str, request: Request, response: Response,
                                 session: AsyncSession = Depends(get_session)):
    job = await _get_export_job(request, session, job_id)
    await export_jobs.cancel_job(session, job)
    response.headers["Cache-Control"] = "no-store"
    return {"status": job.status, "cancel_requested": job.cancel_requested}


@router.get("/linked-products/exports/{job_id}/files/{filename}")
async def download_catalog_export(job_id: str, filename: str, request: Request,
                                   session: AsyncSession = Depends(get_session)):
    job = await _get_export_job(request, session, job_id)
    await catalog_stock_auth.require_quantity_permission(request, session, job.options.get('show_stock') is True)
    return _catalog_export_file(job, filename)


@router.post("/linked-products/exports/{job_id}/files/{filename}/download")
async def direct_catalog_download(job_id: str, filename: str, request: Request,
                                  token: str = Form(..., min_length=1, max_length=128),
                                  session: AsyncSession = Depends(get_session)):
    # Native form download: capability stays in the POST body, never in URLs or cookies.
    job = await _get_export_job(request, session, job_id, token=token)
    await catalog_stock_auth.require_quantity_permission(request, session, job.options.get('show_stock') is True)
    return _catalog_export_file(job, filename)


def _catalog_export_file(job, filename):
    result = job.result or {}
    names = {item["name"] for item in result.get("files", [])}
    if job.status != "ready" or filename not in names:
        raise HTTPException(404, "Export file not found.")
    path = export_jobs.safe_directory(result["directory"]) / filename
    if not path.is_file():
        raise HTTPException(410, "Export file is no longer available. Generate a new catalog.")
    return FileResponse(path, filename=filename, media_type="application/zip" if filename.endswith('.zip') else "application/pdf",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "Referrer-Policy": "no-referrer"})


@router.post("/linked-products/export.pdf")
async def export_catalog_pdf():
    # Intentionally no body validation, database dependency, image or renderer work.
    raise HTTPException(410, "Direct PDF generation is retired. Use /linked-products/exports and its status/download endpoints.",
                        headers={"Cache-Control": "no-store"})


@router.get("/linked-products", response_class=HTMLResponse)
async def linked_products_page(request: Request, session: AsyncSession = Depends(get_session)):
    catalog_identity.bootstrap(request)
    jenis_bahan = request.query_params.get("jenis_bahan", "")
    family_code = request.query_params.get("family_code", "")
    variants = request.query_params.getlist("variants")
    if not variants:
        variants_param = request.query_params.get("variants", "")
        variants = [variants_param] if variants_param else []
    min_stock_raw = request.query_params.get("min_stock", "1")
    try:
        min_stock = max(int(min_stock_raw), 1)
    except ValueError:
        min_stock = 1
    page_raw = request.query_params.get("page", "1")
    try:
        page = max(int(page_raw), 1)
    except ValueError:
        page = 1
    per_page_raw = request.query_params.get("per_page", str(LINKED_PRODUCTS_PER_PAGE))
    try:
        per_page = int(per_page_raw)
    except ValueError:
        per_page = LINKED_PRODUCTS_PER_PAGE
    if per_page not in ALLOWED_LINKED_PRODUCTS_PER_PAGE:
        per_page = LINKED_PRODUCTS_PER_PAGE

    filter_error = None
    try:
        result = await product_service.list_linked_product_families(
            session,
            jenis_bahan=jenis_bahan,
            family_code=family_code,
            variants=variants,
            min_stock=min_stock,
            page=page,
            per_page=per_page,
        )
    except ValueError as exc:
        result = {
            "family_cards": [],
            "total": 0,
            "page": 1,
            "per_page": 25,
            "total_pages": 1,
        }
        filter_error = str(exc)

    return templates.TemplateResponse(request, "linked_products.html", {
        "family_cards": result["family_cards"],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
        "total_pages": result["total_pages"],
        "jenis_bahan": jenis_bahan,
        "jenis_bahan_options": await product_service.list_catalog_materials(session),
        "family_code": family_code,
        "variants": ", ".join(variants),
        "min_stock": min_stock,
        "per_page": per_page,
        "filter_error": filter_error,
        "can_export_quantities": await catalog_stock_auth.can_export_quantities(request, session),
    }, headers={"Cache-Control": "no-store"})
