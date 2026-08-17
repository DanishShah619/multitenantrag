from functools import lru_cache

from app.config import get_settings

settings = get_settings()

BUCKET = "tenant-documents"


@lru_cache
def _client():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_key)


def upload_file(tenant_id: str, document_id: str, filename: str, file_bytes: bytes) -> str:
    """Returns the storage path. Namespaced by tenant so a bucket listing
    alone never mixes files across tenants."""
    path = f"{tenant_id}/{document_id}/{filename}"
    _client().storage.from_(BUCKET).upload(
        path, file_bytes, file_options={"content-type": "application/octet-stream", "upsert": "true"}
    )
    return path


def download_file(storage_path: str) -> bytes:
    return _client().storage.from_(BUCKET).download(storage_path)
