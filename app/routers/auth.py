from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.services.shopee_auth import exchange_code_for_token
from app.services.shopee_client import build_auth_url

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/login")
async def login():
    """Get Shopee OAuth authorization URL. Open the URL in a browser to authorize."""
    url = build_auth_url()
    return {"auth_url": url}


@router.get("/callback")
async def callback(
    code: str = Query(...),
    shop_id: int = Query(...),
):
    """Handle Shopee OAuth callback — exchange code for tokens."""
    try:
        data = await exchange_code_for_token(code, shop_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "message": "Authorization successful",
        "shop_id": shop_id,
        "access_token_preview": (data.get("access_token") or "")[:20] + "...",
    }
