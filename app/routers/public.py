from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services import product_service

router = APIRouter(tags=["public"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters["idr"] = lambda value: "-" if value is None else f"{float(value):,.0f}".replace(",", ".")

LINKED_PRODUCTS_PER_PAGE = 10
ALLOWED_LINKED_PRODUCTS_PER_PAGE = {10, 20, 50}


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
