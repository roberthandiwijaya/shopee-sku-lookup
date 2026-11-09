import asyncio
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services import catalog_pdf_service, catalog_export_service as export_jobs, product_service
from app.config import settings

router = APIRouter(tags=["public"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters["idr"] = lambda value: "-" if value is None else f"{float(value):,.0f}".replace(",", ".")

LINKED_PRODUCTS_PER_PAGE = 10
ALLOWED_LINKED_PRODUCTS_PER_PAGE = {10, 20, 50}
_export_gate = asyncio.Lock()


class CatalogExportRequest(BaseModel):
    family_codes: list[str] = Field(min_length=1, max_length=catalog_pdf_service.MAX_FAMILIES)
    stock_mode: Literal["all", "available"] = "available"
    min_stock: int = Field(default=1, ge=1, le=1_000_000)
    show_stock: bool = True
    show_prices: bool = True


class BackgroundExportRequest(CatalogExportRequest):
    family_codes: list[str] = Field(min_length=1, max_length=250)
    output_mode: Literal["single", "split"] = "single"


@router.post("/linked-products/exports", status_code=202)
async def create_catalog_export(payload: BackgroundExportRequest, request: Request, response: Response,
                                session: AsyncSession = Depends(get_session)):
    owner = request.session.get("catalog_export_owner")
    if not owner:
        import secrets
        owner = secrets.token_hex(16)
        request.session["catalog_export_owner"] = owner
    try:
        job, token = await export_jobs.create_job(session, payload.model_dump(), export_jobs.digest(owner))
    except export_jobs.QueueFull as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc
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
            "snapshot_at": job.snapshot_at, "files": (job.result or {}).get("files", []),
            "omitted": (job.result or {}).get("omitted", 0)}


@router.post("/linked-products/exports/{job_id}/cancel")
async def cancel_catalog_export(job_id: str, request: Request, response: Response,
                                 session: AsyncSession = Depends(get_session)):
    job = await _get_export_job(request, session, job_id)
    await export_jobs.cancel_job(session, job)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "cancelled" if job.status in {"pending", "running", "cancelled"} else job.status}


@router.get("/linked-products/exports/{job_id}/files/{filename}")
async def download_catalog_export(job_id: str, filename: str, request: Request,
                                   session: AsyncSession = Depends(get_session)):
    job = await _get_export_job(request, session, job_id)
    return _catalog_export_file(job, filename)


@router.post("/linked-products/exports/{job_id}/files/{filename}/download")
async def direct_catalog_download(job_id: str, filename: str, request: Request,
                                  token: str = Form(..., min_length=1, max_length=128),
                                  session: AsyncSession = Depends(get_session)):
    # Native form download: capability stays in the POST body, never in URLs or cookies.
    job = await _get_export_job(request, session, job_id, token=token)
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
async def export_catalog_pdf(payload: CatalogExportRequest, session: AsyncSession = Depends(get_session)):
    # Same public data as the catalog. Bound work to one export per app process.
    if _export_gate.locked():
        raise HTTPException(429, "Another catalog export is running. Please try again shortly.", headers={"Retry-After": "10"})
    async with _export_gate:
        try:
            cards, omitted = await catalog_pdf_service.get_export_cards(
                session, payload.family_codes, stock_mode=payload.stock_mode, min_stock=payload.min_stock,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        images = await catalog_pdf_service.fetch_catalog_images(cards)
        pdf = await run_in_threadpool(
            catalog_pdf_service.render_catalog_pdf, cards, images=images,
            show_stock=payload.show_stock, show_prices=payload.show_prices, stock_mode=payload.stock_mode,
            min_stock=payload.min_stock, omitted=omitted,
        )
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="product-catalog.pdf"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "X-Catalog-Omitted-Families": str(omitted),
    })


@router.get("/linked-products", response_class=HTMLResponse)
async def linked_products_page(request: Request, session: AsyncSession = Depends(get_session)):
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
    })
