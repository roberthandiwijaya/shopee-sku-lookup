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
    family_cards = await product_service.list_linked_product_families(session)
    return templates.TemplateResponse(request, "linked_products.html", {
        "family_cards": family_cards,
    })
