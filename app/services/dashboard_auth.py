import hashlib
import secrets

from fastapi import Request


class LoginRequired(Exception):
    """Raised when a dashboard route is accessed without a valid session."""
    pass


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a password with PBKDF2. Returns (hash_hex, salt_hex)."""
    if salt is None:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()
    return pw_hash, salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Verify a password against a stored hash and salt."""
    candidate, _ = hash_password(password, salt)
    return secrets.compare_digest(candidate, password_hash)


async def require_login(request: Request):
    """FastAPI dependency that enforces dashboard login via session."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise LoginRequired()
