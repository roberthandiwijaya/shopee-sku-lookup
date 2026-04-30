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

    filter_error = None
    try:
        family_cards = await product_service.list_linked_product_families(
            session,
            jenis_bahan=jenis_bahan,
            family_code=family_code,
            variants=variants,
            min_stock=min_stock,
        )
    except ValueError as exc:
        family_cards = []
        filter_error = str(exc)

    return templates.TemplateResponse(request, "linked_products.html", {
        "family_cards": family_cards,
        "jenis_bahan": jenis_bahan,
        "family_code": family_code,
        "variants": ", ".join(variants),
        "min_stock": min_stock,
        "filter_error": filter_error,
    })
