"""Exact PDF inventory is an admin-only export option, not a browser-only flag."""
from fastapi import HTTPException, Request

from app.config import settings
from app.models.product import DashboardUser


async def can_export_quantities(request: Request, session) -> bool:
    user_id = request.session.get('user_id')
    if not isinstance(user_id, int):
        return False
    user = await session.get(DashboardUser, user_id)
    return bool(user and not user.must_change_password and user.username in settings.catalog_export_admin_usernames)


async def require_quantity_permission(request: Request, session, show_stock: bool):
    if show_stock and not await can_export_quantities(request, session):
        raise HTTPException(403, 'Ekspor jumlah stok dalam pcs hanya untuk akun admin. Silakan masuk sebagai admin.')
    return request.session.get('user_id') if show_stock else None
