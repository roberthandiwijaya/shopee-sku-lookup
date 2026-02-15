import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models.product import Product
from app.services import product_service
from app.services.shopee_client import build_auth_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

WIB = timezone(timedelta(hours=7))


def _format_wib(dt: datetime | None, fmt: str = "%d %b %Y %H:%M") -> str:
    """Convert a datetime to WIB (UTC+7) and format it."""
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    else:
        dt = dt.astimezone(WIB)
    return dt.strftime(fmt) + " WIB"


templates.env.filters["wib"] = _format_wib


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    """Main dashboard page — serves the full HTML layout."""
    sync_stats = await product_service.get_sync_stats(session)
    token_details = await product_service.get_token_details(session)
    auth_url = build_auth_url()
    paginated = await product_service.list_products_paginated(session)

    return templates.TemplateResponse(request, "dashboard.html", {
        "sync_stats": sync_stats,
        "token_details": token_details,
        "auth_url": auth_url,
        **paginated,
    })


@router.get("/partials/sync-status", response_class=HTMLResponse)
async def partial_sync_status(request: Request, session: AsyncSession = Depends(get_session)):
    """Sync status panel fragment (polled by htmx every 30s)."""
    sync_stats = await product_service.get_sync_stats(session)
    return templates.TemplateResponse(request, "partials/sync_status.html", {
        "sync_stats": sync_stats,
    })


@router.get("/partials/auth", response_class=HTMLResponse)
async def partial_auth(request: Request, session: AsyncSession = Depends(get_session)):
    """Auth panel fragment (polled by htmx every 30s)."""
    token_details = await product_service.get_token_details(session)
    auth_url = build_auth_url()
    return templates.TemplateResponse(request, "partials/auth_panel.html", {
        "token_details": token_details,
        "auth_url": auth_url,
    })


@router.get("/partials/products", response_class=HTMLResponse)
async def partial_products(
    request: Request,
    page: int = 1,
    search: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Product rows fragment (htmx search + pagination)."""
    paginated = await product_service.list_products_paginated(
        session, page=page, per_page=25, search_sku=search or None,
    )
    return templates.TemplateResponse(request, "partials/product_rows.html", {
        "search": search,
        **paginated,
    })


@router.get("/partials/products/{item_id}/models", response_class=HTMLResponse)
async def partial_product_models(
    request: Request,
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Variant rows fragment for expanding a product."""
    stmt = (
        select(Product)
        .options(selectinload(Product.models))
        .where(Product.item_id == item_id)
    )
    result = await session.execute(stmt)
    product = result.scalar_one_or_none()
    models = product.models if product else []

    return templates.TemplateResponse(request, "partials/product_models.html", {
        "models": models,
    })


@router.post("/partials/sync-trigger", response_class=HTMLResponse)
async def partial_sync_trigger(request: Request):
    """Trigger a manual sync and return feedback fragment."""
    from app.services.sync_service import run_sync

    try:
        await run_sync()
        status = "success"
        message = "Sync completed successfully"
    except Exception as e:
        logger.exception("Manual sync failed")
        status = "error"
        message = f"Sync failed: {e}"

    return templates.TemplateResponse(request, "partials/sync_result.html", {
        "status": status,
        "message": message,
    })
