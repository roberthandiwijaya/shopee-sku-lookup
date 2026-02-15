from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.schemas.product import ProductSearchResponse, SyncStatusResponse, SyncTriggerResponse
from app.services import product_service

router = APIRouter(prefix="/api", tags=["products"])

api_key_header = APIKeyHeader(name="X-API-Key")


async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


@router.get("/products", response_model=ProductSearchResponse, dependencies=[Depends(verify_api_key)])
async def get_products_by_sku(
    sku: list[str] = Query(..., description="One or more SKUs (repeatable or comma-separated)"),
    session: AsyncSession = Depends(get_session),
):
    """Look up products by SKU. Supports multiple SKUs via repeated `sku=` params or comma-separated values."""
    all_skus: list[str] = []
    for s in sku:
        all_skus.extend(part.strip() for part in s.split(",") if part.strip())

    return await product_service.lookup_by_skus(session, all_skus)


@router.get("/sync/status", response_model=SyncStatusResponse)
async def sync_status(session: AsyncSession = Depends(get_session)):
    """Get the current sync status."""
    stats = await product_service.get_sync_stats(session)
    return SyncStatusResponse(**stats)


@router.post("/sync", response_model=SyncTriggerResponse, dependencies=[Depends(verify_api_key)])
async def trigger_sync():
    """Trigger a manual product sync from Shopee."""
    from app.services.sync_service import run_sync

    try:
        await run_sync()
        return SyncTriggerResponse(message="Sync completed successfully", status="success")
    except Exception as e:
        return SyncTriggerResponse(message=f"Sync failed: {e}", status="error")
