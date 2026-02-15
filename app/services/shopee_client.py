import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds


async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, max_retries: int = MAX_RETRIES, **kwargs) -> httpx.Response:
    """Execute an HTTP request with retry logic for transient errors (5xx, timeouts)."""
    resp = None
    for attempt in range(max_retries):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code < 500:
                return resp
            logger.warning("Shopee API %s %s returned %d (attempt %d/%d)", method, url, resp.status_code, attempt + 1, max_retries)
        except httpx.TimeoutException:
            logger.warning("Shopee API timeout %s %s (attempt %d/%d)", method, url, attempt + 1, max_retries)
        if attempt < max_retries - 1:
            await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))
    if resp is None:
        raise httpx.TimeoutException(f"All {max_retries} attempts failed for {method} {url}")
    return resp


def _make_sign(path: str, timestamp: int, access_token: str = "", shop_id: int = 0) -> str:
    base_string = f"{settings.shopee_partner_id}{path}{timestamp}"
    if access_token and shop_id:
        base_string += f"{access_token}{shop_id}"
    return hmac.new(
        settings.shopee_partner_key.encode(), base_string.encode(), hashlib.sha256
    ).hexdigest()


def _build_url(path: str, access_token: str = "", shop_id: int = 0) -> tuple[str, dict[str, Any]]:
    timestamp = int(time.time())
    sign = _make_sign(path, timestamp, access_token, shop_id)
    params: dict[str, Any] = {
        "partner_id": settings.shopee_partner_id,
        "timestamp": timestamp,
        "sign": sign,
    }
    if access_token and shop_id:
        params["access_token"] = access_token
        params["shop_id"] = shop_id
    url = f"{settings.shopee_base_url}{path}"
    return url, params


async def public_request(
    path: str, method: str = "GET", params: dict[str, Any] | None = None, **kwargs
) -> dict:
    url, base_params = _build_url(path)
    if params:
        base_params.update(params)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await _request_with_retry(client, method, url, params=base_params, **kwargs)
        data = resp.json()
        if data.get("error"):
            logger.error("Shopee API error %s %s: %s", method, path, data)
        return data


async def shop_request(
    path: str, access_token: str, shop_id: int, method: str = "GET",
    params: dict[str, Any] | None = None, **kwargs
) -> dict:
    url, base_params = _build_url(path, access_token, shop_id)
    if params:
        base_params.update(params)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await _request_with_retry(client, method, url, params=base_params, **kwargs)
        data = resp.json()
        if data.get("error"):
            logger.error("Shopee API error %s %s: %s", method, path, data)
        return data


def build_auth_url() -> str:
    path = "/api/v2/shop/auth_partner"
    timestamp = int(time.time())
    sign = _make_sign(path, timestamp)
    redirect = settings.shopee_redirect_url
    return (
        f"{settings.shopee_base_url}{path}"
        f"?partner_id={settings.shopee_partner_id}"
        f"&timestamp={timestamp}"
        f"&sign={sign}"
        f"&redirect={redirect}"
    )
