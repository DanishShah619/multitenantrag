"""
Deletes demo tenants older than DEMO_TENANT_TTL_DAYS, along with their
documents, chats, and API keys (cascade delete handles the DB side) and
their Milvus chunks (handled explicitly below, since Milvus isn't part of
the Postgres cascade).

NOT scheduled automatically - run manually, or wire up as a Render Cron
Job / GitHub Action on a schedule if you want this automated. Deliberately
manual-first: automatic deletion of user data deserves a human to have
set it up on purpose, not run silently by default.

Usage:
    python -m scripts.cleanup_demo_tenants          # dry run, lists what would be deleted
    python -m scripts.cleanup_demo_tenants --confirm # actually deletes
"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from app.database import SessionLocal
from app.models import Tenant
from app.config import get_settings
from app.milvus_client import delete_document_chunks

settings = get_settings()


def main(confirm: bool):
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=settings.demo_tenant_ttl_days)
        expired = (
            db.query(Tenant)
            .filter(Tenant.is_demo.is_(True), Tenant.created_at < cutoff)
            .all()
        )

        if not expired:
            print("No expired demo tenants found.")
            return

        print(f"Found {len(expired)} expired demo tenant(s):")
        for t in expired:
            print(f"  {t.id} - {t.name} (created {t.created_at})")

        if not confirm:
            print("\nDry run - re-run with --confirm to actually delete.")
            return

        for t in expired:
            for doc in t.documents:
                delete_document_chunks(tenant_id=t.id, document_id=doc.id)
            db.delete(t)  # cascades to documents, api_keys, chats, messages
        db.commit()
        print(f"\nDeleted {len(expired)} demo tenant(s) and their data.")

    finally:
        db.close()


if __name__ == "__main__":
    main(confirm="--confirm" in sys.argv)
