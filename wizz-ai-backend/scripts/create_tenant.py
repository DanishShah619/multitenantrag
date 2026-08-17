"""
Run this once to create a test tenant and admin API key:

    python -m scripts.create_tenant "Acme Corp"

Prints the raw API key ONCE - copy it, it's not recoverable afterward
(only the hash is stored).
"""
import sys

sys.path.insert(0, ".")

from app.database import SessionLocal, Base, engine
from app.models import Tenant, APIKey
from app.auth import generate_api_key


def main(tenant_name: str):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        tenant = Tenant(name=tenant_name)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        raw_admin_key, admin_hash, admin_prefix = generate_api_key(scope="admin")
        db.add(APIKey(tenant_id=tenant.id, key_hash=admin_hash, key_prefix=admin_prefix, scope="admin"))

        raw_embed_key, embed_hash, embed_prefix = generate_api_key(scope="embed")
        db.add(APIKey(tenant_id=tenant.id, key_hash=embed_hash, key_prefix=embed_prefix, scope="embed"))

        db.commit()

        print(f"Tenant created: {tenant.id} ({tenant.name})")
        print(f"Admin API key   (use for /documents uploads): {raw_admin_key}")
        print(f"Embed API key   (use for /chat, widget-facing): {raw_embed_key}")
        print("Both shown once - save them now.")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.create_tenant \"Tenant Name\"")
        sys.exit(1)
    main(sys.argv[1])
