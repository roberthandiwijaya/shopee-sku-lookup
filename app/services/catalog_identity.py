"""Guest identity and explicit proxy trust for bounded public export admission."""
import hashlib
import hmac
import ipaddress
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings


def bootstrap(request: Request) -> str:
    if not request.session.get("catalog_export_owner"):
        request.session["catalog_export_owner"] = secrets.token_hex(16)
    if not request.session.get("catalog_export_csrf"):
        request.session["catalog_export_csrf"] = secrets.token_urlsafe(32)
    return request.session["catalog_export_csrf"]


def _origin(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname.lower(), port
    except (ValueError, TypeError):
        return None


def require_export_session(request: Request) -> str:
    """Require a prior signed-cookie round trip; never admit a cookie-less creation."""
    owner = request.session.get("catalog_export_owner")
    csrf = request.session.get("catalog_export_csrf")
    supplied = request.headers.get("X-Export-CSRF", "")
    if not owner or not csrf:
        raise HTTPException(409, "Reload the catalog page to initialize your export session.")
    if not secrets.compare_digest(csrf.encode(), supplied.encode()):
        raise HTTPException(403, "Invalid export token. Reload the catalog page.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site export requests are not allowed.")
    source = request.headers.get("origin") or request.headers.get("referer")
    allowed = {_origin(value) for value in settings.catalog_export_allowed_origins}
    allowed.discard(None)
    if source is None or _origin(source) not in allowed:
        raise HTTPException(403, "Export request origin is not allowed.")
    return hashlib.sha256(owner.encode()).hexdigest()


def client_network(request: Request) -> str:
    """Run with Uvicorn --no-proxy-headers so request.client is the socket peer.

    Only exact configured proxy addresses may supply XFF. Walk the validated
    chain from the trusted right side, never use an arbitrary leftmost value.
    """
    try:
        peer = ipaddress.ip_address(request.client.host)
    except (AttributeError, ValueError):
        raise HTTPException(503, "Client network identity is unavailable.") from None
    try:
        trusted = {ipaddress.ip_address(value) for value in settings.catalog_export_trusted_proxy_ips}
    except ValueError:
        raise HTTPException(503, "Export proxy trust configuration is invalid.") from None
    address = peer
    if peer not in trusted and not peer.is_global:
        # Refuse proxy/gateway collapse instead of pretending one shared private
        # address is a verified end-user network. Loopback supports explicit local dev.
        if not (peer.is_loopback and not settings.session_cookie_secure):
            raise HTTPException(503, "Configure an exact trusted proxy before enabling exports.")
    if peer in trusted:
        forwarded = request.headers.get("x-forwarded-for", "")
        if not forwarded or len(forwarded) > 2048:
            raise HTTPException(503, "Trusted proxy did not provide a valid client address.")
        try:
            chain = [ipaddress.ip_address(part.strip()) for part in forwarded.split(",")]
            if not 1 <= len(chain) <= 16:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid forwarded client address.") from None
        for candidate in reversed(chain):
            address = candidate
            if candidate not in trusted:
                break
    # IPv4-mapped IPv6 must share its IPv4 bucket.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    prefix = 32 if address.version == 4 else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def network_hash(request: Request) -> str:
    if len(settings.catalog_export_network_secret) < 32:
        raise HTTPException(503, "Export network protection is not configured.")
    return hmac.new(settings.catalog_export_network_secret.encode(), client_network(request).encode(), hashlib.sha256).hexdigest()


def limited_response(exc):
    return JSONResponse(status_code=429,
        content={"detail": str(exc), "reason": exc.reason, "retry_after": exc.retry_after},
        headers={"Retry-After": str(exc.retry_after), "Cache-Control": "no-store"})
