import hashlib
import secrets

from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import APIKey


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key(scope: str = "admin") -> tuple[str, str, str]:
    """
    Returns (raw_key, key_hash, key_prefix).
    raw_key is shown to the user ONCE at creation time and never stored.
    """
    raw = f"wizz_{scope}_{secrets.token_urlsafe(32)}"
    return raw, hash_key(raw), raw[:12]


def require_tenant(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> str:
    """
    FastAPI dependency: validates the API key and returns the tenant_id.
    Use this on every tenant-scoped route so tenant_id is never taken
    from the request body/query params (which a client could tamper with).
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    key_hash = hash_key(x_api_key)
    key_row = (
        db.query(APIKey)
        .filter(APIKey.key_hash == key_hash, APIKey.revoked_at.is_(None))
        .first()
    )
    if not key_row:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    return key_row.tenant_id


def require_embed_scope(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> str:
    """Stricter dependency for the public widget - only accepts 'embed' scoped keys."""
    key_hash = hash_key(x_api_key)
    key_row = (
        db.query(APIKey)
        .filter(APIKey.key_hash == key_hash, APIKey.revoked_at.is_(None))
        .first()
    )
    if not key_row or key_row.scope != "embed":
        raise HTTPException(status_code=401, detail="Invalid embed key")

    return key_row.tenant_id
