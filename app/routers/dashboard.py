import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_session
from app.models.discount import Discount
from app.models.product import Product
from app.services import discount_service, product_service
from app.services.dashboard_auth import require_login
from app.services.shopee_auth import get_valid_token
from app.services.shopee_client import build_auth_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_login)])

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


def _format_currency(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.0f}".replace(",", ".")


templates.env.filters["wib"] = _format_wib
templates.env.filters["idr"] = _format_currency


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    """Main dashboard page — serves the full HTML layout."""
    sync_stats = await product_service.get_sync_stats(session)
    token_details = await product_service.get_token_details(session)
    auth_url = build_auth_url()
    paginated = await product_service.list_products_paginated(session)
    alert = await product_service.get_active_alert(session)
    shop_info = await product_service.get_shop_info(session)

    return templates.TemplateResponse(request, "dashboard.html", {
        "sync_stats": sync_stats,
        "token_details": token_details,
        "auth_url": auth_url,
        "alert": alert,
        "shop_info": shop_info,
        **paginated,
    })


@router.get("/discounts", response_class=HTMLResponse)
async def discounts_page(request: Request, session: AsyncSession = Depends(get_session)):
    """Discounts page — full HTML layout."""
    discount_stats = await discount_service.get_discount_sync_stats(session)
    discount_paginated = await discount_service.list_discounts(session)

    return templates.TemplateResponse(request, "discounts.html", {
        "discount_stats": discount_stats,
        "discount_page": discount_paginated["page"],
        "discount_total": discount_paginated["total"],
        "discount_total_pages": discount_paginated["total_pages"],
        "discount_status": None,
        "discounts": discount_paginated["discounts"],
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
    shop_info = await product_service.get_shop_info(session)
    return templates.TemplateResponse(request, "partials/auth_panel.html", {
        "token_details": token_details,
        "auth_url": auth_url,
        "shop_info": shop_info,
    })


@router.get("/partials/product-table", response_class=HTMLResponse)
async def partial_product_table(
    request: Request,
    page: int = 1,
    search: str = "",
    partner_filter: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Full product table fragment with filter state."""
    paginated = await product_service.list_products_paginated(
        session,
        page=page,
        per_page=25,
        search_sku=search or None,
        partner_filter=partner_filter or None,
    )
    return templates.TemplateResponse(request, "partials/product_table.html", {
        "search": search,
        "partner_filter": partner_filter,
        **paginated,
    })


@router.get("/partials/products", response_class=HTMLResponse)
async def partial_products(
    request: Request,
    page: int = 1,
    search: str = "",
    partner_filter: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Product rows fragment (htmx search + pagination)."""
    paginated = await product_service.list_products_paginated(
        session,
        page=page,
        per_page=25,
        search_sku=search or None,
        partner_filter=partner_filter or None,
    )
    return templates.TemplateResponse(request, "partials/product_rows.html", {
        "search": search,
        "partner_filter": partner_filter,
        **paginated,
    })


@router.get("/partials/products/{item_id}/models", response_class=HTMLResponse)
async def partial_product_models(
    request: Request,
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Variant and related-product rows fragment for expanding a product."""
    detail = await product_service.get_related_products(session, item_id)

    return templates.TemplateResponse(request, "partials/product_models.html", {
        "page": request.query_params.get("page", "1"),
        "search": request.query_params.get("search", ""),
        "partner_filter": request.query_params.get("partner_filter", ""),
        **detail,
    })


@router.get("/partials/alert-banner", response_class=HTMLResponse)
async def partial_alert_banner(request: Request, session: AsyncSession = Depends(get_session)):
    """Alert banner fragment (polled by htmx every 30s)."""
    alert = await product_service.get_active_alert(session)
    auth_url = build_auth_url()
    return templates.TemplateResponse(request, "partials/alert_banner.html", {
        "alert": alert,
        "auth_url": auth_url,
    })


@router.post("/partials/alert-dismiss/{alert_id}", response_class=HTMLResponse)
async def partial_alert_dismiss(
    request: Request, alert_id: int, session: AsyncSession = Depends(get_session)
):
    """Dismiss an alert and return empty HTML."""
    await product_service.dismiss_alert(session, alert_id)
    return HTMLResponse("")


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


@router.get("/partials/discounts", response_class=HTMLResponse)
async def partial_discounts(
    request: Request,
    page: int = 1,
    status: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Discount rows fragment (htmx filter + pagination)."""
    paginated = await discount_service.list_discounts(
        session, page=page, per_page=25, status_filter=status or None
    )
    return templates.TemplateResponse(request, "partials/discount_rows.html", {
        "discount_page": paginated["page"],
        "discount_total": paginated["total"],
        "discount_total_pages": paginated["total_pages"],
        "discount_status": status or None,
        "discounts": paginated["discounts"],
    })


@router.get("/partials/discounts/{discount_id}/items", response_class=HTMLResponse)
async def partial_discount_items(
    request: Request,
    discount_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Expandable items sub-table for a single discount."""
    discount = await discount_service.get_discount_detail(session, discount_id)
    items = await discount_service.get_enriched_discount_items(session, discount.id) if discount else []
    return templates.TemplateResponse(request, "partials/discount_items.html", {
        "items": items,
    })


@router.get("/discounts/{discount_id}/edit", response_class=HTMLResponse)
async def discount_edit_page(
    request: Request,
    discount_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Edit page for a single discount."""
    discount = await discount_service.get_discount_detail(session, discount_id)
    if not discount:
        return HTMLResponse("Discount not found", status_code=404)

    price_overrides = None
    if discount.discount_status == "ongoing":
        try:
            access_token = await get_valid_token(settings.shopee_shop_id)
            if access_token:
                price_overrides = await discount_service.fetch_live_discount_prices(
                    settings.shopee_shop_id, access_token, discount.discount_id
                )
        except Exception:
            # If API fails, use database values (price_overrides stays None)
            pass

    items = await discount_service.get_enriched_discount_items(
        session, discount.id, price_overrides=price_overrides
    )
    return templates.TemplateResponse(request, "discounts_edit.html", {
        "discount": discount,
        "items": items,
    })


@router.post("/discounts/{discount_id}/edit")
async def discount_edit_submit(
    request: Request,
    discount_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Submit edits for a discount."""
    discount = await discount_service.get_discount_detail(session, discount_id)
    if not discount:
        return HTMLResponse("Discount not found", status_code=404)

    form = await request.form()
    access_token = await get_valid_token(settings.shopee_shop_id)

    # Update basic info
    discount_name = form.get("discount_name")
    end_time_str = form.get("end_time")
    end_time_ts = None
    if end_time_str:
        try:
            parsed = datetime.fromisoformat(end_time_str)
            end_time_ts = int(parsed.timestamp())
        except ValueError:
            pass

    await discount_service.update_discount(
        settings.shopee_shop_id,
        access_token,
        discount_id,
        name=discount_name,
        end_time=end_time_ts,
    )

    # Scan the form for all item_id_* keys to build item_list dynamically
    form_keys = [k[len("item_id_"):] for k in form.keys() if k.startswith("item_id_")]
    item_list = []
    for fk in form_keys:
        item_id = int(form[f"item_id_{fk}"])
        model_id = int(form[f"model_id_{fk}"])
        price_val = form.get(f"price_{fk}", "").strip()
        limit_val = form.get(f"limit_{fk}", "").strip()
        if price_val:
            entry = {"item_id": item_id, "item_promotion_price": float(price_val)}
            if model_id != 0:
                entry["model_id"] = model_id
            if limit_val:
                entry["purchase_limit"] = int(limit_val)
            item_list.append(entry)

    if item_list:
        await discount_service.update_discount_items(
            settings.shopee_shop_id, access_token, discount_id, item_list
        )

    return RedirectResponse("/discounts", status_code=303)


@router.post("/discounts/{discount_id}/delete")
async def discount_delete(
    request: Request,
    discount_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Delete a discount."""
    access_token = await get_valid_token(settings.shopee_shop_id)
    await discount_service.delete_discount(settings.shopee_shop_id, access_token, discount_id)
    return RedirectResponse("/discounts", status_code=303)


@router.get("/partials/products/{item_id}/family-code/edit", response_class=HTMLResponse)
async def partial_product_family_code_edit(
    request: Request,
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Inline editor for a single product family code."""
    product = await product_service.get_product_by_item_id(session, item_id)
    if not product:
        return HTMLResponse("Product not found", status_code=404)

    return templates.TemplateResponse(request, "partials/product_family_code_form.html", {
        "product": product,
        "error": None,
        "submitted_value": None,
        "page": request.query_params.get("page", "1"),
        "search": request.query_params.get("search", ""),
        "partner_filter": request.query_params.get("partner_filter", ""),
    })


@router.post("/partials/products/{item_id}/family-code/edit", response_class=HTMLResponse)
async def partial_product_family_code_edit_submit(
    request: Request,
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Save an inline family code edit and return the updated detail panel."""
    product = await product_service.get_product_by_item_id(session, item_id)
    if not product:
        return HTMLResponse("Product not found", status_code=404)

    form = await request.form()
    family_code = form.get("family_code")

    try:
        await product_service.update_product_family_code(session, item_id, family_code)
    except ValueError as exc:
        return templates.TemplateResponse(request, "partials/product_family_code_form.html", {
            "product": product,
            "error": str(exc),
            "submitted_value": family_code,
            "page": request.query_params.get("page", "1"),
            "search": request.query_params.get("search", ""),
            "partner_filter": request.query_params.get("partner_filter", ""),
        })

    detail = await product_service.get_related_products(session, item_id)
    return templates.TemplateResponse(request, "partials/product_models.html", {
        "page": request.query_params.get("page", "1"),
        "search": request.query_params.get("search", ""),
        "partner_filter": request.query_params.get("partner_filter", ""),
        **detail,
    })


@router.get("/partials/products/{item_id}/jenis-bahan/edit", response_class=HTMLResponse)
async def partial_product_jenis_bahan_edit(
    request: Request,
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Inline editor for a single product jenis bahan."""
    product = await product_service.get_product_by_item_id(session, item_id)
    if not product:
        return HTMLResponse("Product not found", status_code=404)

    return templates.TemplateResponse(request, "partials/product_jenis_bahan_form.html", {
        "product": product,
        "error": None,
        "submitted_value": None,
        "page": request.query_params.get("page", "1"),
        "search": request.query_params.get("search", ""),
        "partner_filter": request.query_params.get("partner_filter", ""),
    })


@router.post("/partials/products/{item_id}/jenis-bahan/edit", response_class=HTMLResponse)
async def partial_product_jenis_bahan_edit_submit(
    request: Request,
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Save an inline jenis bahan edit and return the updated detail panel."""
    product = await product_service.get_product_by_item_id(session, item_id)
    if not product:
        return HTMLResponse("Product not found", status_code=404)

    form = await request.form()
    jenis_bahan = form.get("jenis_bahan")

    try:
        await product_service.update_product_jenis_bahan(session, item_id, jenis_bahan)
    except ValueError as exc:
        return templates.TemplateResponse(request, "partials/product_jenis_bahan_form.html", {
            "product": product,
            "error": str(exc),
            "submitted_value": jenis_bahan,
            "page": request.query_params.get("page", "1"),
            "search": request.query_params.get("search", ""),
            "partner_filter": request.query_params.get("partner_filter", ""),
        }, status_code=400)

    detail = await product_service.get_related_products(session, item_id)
    return templates.TemplateResponse(request, "partials/product_models.html", {
        "page": request.query_params.get("page", "1"),
        "search": request.query_params.get("search", ""),
        "partner_filter": request.query_params.get("partner_filter", ""),
        **detail,
    })


@router.post("/partials/discount-sync-trigger", response_class=HTMLResponse)
async def partial_discount_sync_trigger(request: Request):
    """Trigger a manual discount sync and return feedback fragment."""
    try:
        synced_count = await discount_service.sync_discounts()
        status = "success"
        message = f"Discount sync completed: {synced_count} discounts synced"
    except Exception as e:
        logger.exception("Manual discount sync failed")
        status = "error"
        message = f"Discount sync failed: {e}"

    return templates.TemplateResponse(request, "partials/sync_result.html", {
        "status": status,
        "message": message,
    })
