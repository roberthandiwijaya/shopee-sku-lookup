"""Authenticated, preview-first local catalog bulk editing."""
import csv
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services.dashboard_auth import require_login
from app.services import bulk_product_service as bulk

router = APIRouter(prefix="/products/bulk", dependencies=[Depends(require_login)])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
MAX_BODY = 600_000


def _csrf(request: Request) -> str:
    if "bulk_csrf" not in request.session:
        request.session["bulk_csrf"] = secrets.token_urlsafe(32)
    return request.session["bulk_csrf"]


def _render(request: Request, status_code: int = 200, **context):
    return templates.TemplateResponse(request, "bulk_products.html", {
        "csrf_token": _csrf(request), **context,
    }, status_code=status_code, headers={"Cache-Control": "no-store"})


async def _form(request: Request) -> dict[str, str]:
    # Check browser origin when available, and always require a session-bound token.
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site submission refused.")
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin:
        incoming, expected = urlsplit(origin), urlsplit(str(request.base_url))
        if (incoming.scheme, incoming.netloc) != (expected.scheme, expected.netloc):
            raise HTTPException(403, "Cross-origin submission refused.")
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Use the bulk editor form (URL-encoded fields).")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "Bulk form is too large.")
        body.extend(chunk)
    try:
        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=12)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, "Invalid form.") from exc
    if any(len(values) != 1 for values in fields.values()):
        raise HTTPException(400, "Duplicate form fields.")
    form = {key: values[0] for key, values in fields.items()}
    token = form.get("csrf_token", "")
    expected_token = request.session.get("bulk_csrf", "")
    if not expected_token or not secrets.compare_digest(token.encode(), expected_token.encode()):
        raise HTTPException(403, "Invalid CSRF token. Reload the editor.")
    return form


@router.get("")
async def editor(request: Request):
    return _render(request)


@router.post("/preview")
async def preview(request: Request, session: AsyncSession = Depends(get_session)):
    form = await _form(request)
    try:
        values = {field: form.get(field, "") for field in bulk.FIELDS
                  if form.get("edit_" + field) == "yes"}
        changes = bulk.parse_changes(form.get("item_ids", ""), form.get("csv_text", ""), **values)
        rows, token = await bulk.preview_changes(session, changes, request.session["user_id"])
        return _render(request, rows=rows, preview_token=token, submitted=form)
    except (bulk.BulkEditError, csv.Error) as exc:
        return _render(request, status_code=400, error=str(exc), submitted=form)


@router.post("/apply")
async def apply(request: Request, session: AsyncSession = Depends(get_session)):
    form = await _form(request)
    try:
        if form.get("confirm") != "yes":
            raise bulk.BulkEditError("Confirm the preview before applying.")
        count = await bulk.apply_changes(session, form.get("preview_token", ""), request.session["user_id"])
        return _render(request, success=f"Saved local metadata for {count} products. No Shopee changes were made.")
    except bulk.BulkEditError as exc:
        return _render(request, status_code=409, error=str(exc))
