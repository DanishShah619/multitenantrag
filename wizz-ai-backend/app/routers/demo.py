"""
Public, unauthenticated signup for a recruiter/demo tenant. No account
system, no email verification - just enough friction (IP rate limiting on
signup itself) to keep this from being a free-for-all resource sink, plus
hard per-tenant quotas enforced in the ingestion/chat routers for any
tenant created here (see is_demo checks in routers/ingestion.py and
routers/chat.py).

This endpoint issues real, working API keys - anyone who calls it gets a
functioning (but capped) tenant. That's the intended behavior: a recruiter
should be able to hit this once and immediately have something to test
with curl or Postman, no signup form, no waiting on you.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Tenant, APIKey
from app.auth import generate_api_key
from app.config import get_settings
from app.rate_limit import is_allowed

router = APIRouter(prefix="/demo", tags=["demo"])
settings = get_settings()


class DemoSignupRequest(BaseModel):
    label: str | None = None  # optional, purely cosmetic (shows up as the tenant name)


class DemoSignupResponse(BaseModel):
    tenant_id: str
    admin_api_key: str
    embed_api_key: str
    limits: dict
    message: str


def _client_ip(request: Request) -> str:
    # Render (and most PaaS) sit behind a proxy - the real client IP is in
    # X-Forwarded-For, not request.client.host (which would be the proxy's
    # own address). Fall back to request.client.host for local/direct runs.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/signup", response_model=DemoSignupResponse)
def demo_signup(payload: DemoSignupRequest, request: Request):
    if not settings.demo_enabled:
        raise HTTPException(503, "Demo signups are currently disabled")

    ip = _client_ip(request)
    if not is_allowed(f"demo_signup:{ip}", settings.demo_signups_per_ip_per_day, window_seconds=86400):
        raise HTTPException(
            429,
            f"Signup limit reached for this IP ({settings.demo_signups_per_ip_per_day}/day). "
            "This is a shared public demo - please try again tomorrow, or reach out directly if you need a dedicated demo.",
        )

    db: Session = SessionLocal()
    try:
        tenant = Tenant(name=payload.label or "Demo Tenant", is_demo=True)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        raw_admin_key, admin_hash, admin_prefix = generate_api_key(scope="admin")
        db.add(APIKey(tenant_id=tenant.id, key_hash=admin_hash, key_prefix=admin_prefix, scope="admin"))

        raw_embed_key, embed_hash, embed_prefix = generate_api_key(scope="embed")
        db.add(APIKey(tenant_id=tenant.id, key_hash=embed_hash, key_prefix=embed_prefix, scope="embed"))

        db.commit()

        return DemoSignupResponse(
            tenant_id=tenant.id,
            admin_api_key=raw_admin_key,
            embed_api_key=raw_embed_key,
            limits={
                "max_documents": settings.demo_max_documents,
                "max_upload_mb": settings.demo_max_upload_mb,
                "max_chat_messages_per_hour": settings.demo_max_chat_per_hour,
                "max_chat_messages_per_day": settings.demo_max_chat_per_day,
                "tenant_retention_days": settings.demo_tenant_ttl_days,
            },
            message=(
                "Demo tenant created. Use admin_api_key with X-API-Key header on POST /documents "
                "to upload a document, then embed_api_key on POST /chat to query it. "
                "Both keys are shown once - save them now. This tenant and its data may be "
                f"cleaned up after {settings.demo_tenant_ttl_days} days."
            ),
        )
    finally:
        db.close()
