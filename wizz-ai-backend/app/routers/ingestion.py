from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_tenant
from app.database import get_db
from app.config import get_settings
from app.models import Document, DocumentStatus, Tenant
from app.schemas import DocumentOut, IngestResponse
from app.storage import upload_file
from app.services.ingestion import process_document

router = APIRouter(prefix="/documents", tags=["ingestion"])
settings = get_settings()


@router.post("", response_model=IngestResponse)
async def ingest_document(
    file: UploadFile = File(...),
    source_id: str | None = Form(None),
    tenant_id: str = Depends(require_tenant),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    # Demo tenants get a lower upload size cap and a hard document-count
    # ceiling - both enforced here rather than only at signup time, since
    # quotas need to hold for the tenant's whole lifetime, not just once.
    max_mb = settings.demo_max_upload_mb if tenant.is_demo else settings.max_upload_mb

    if tenant.is_demo:
        existing_count = db.query(Document).filter(Document.tenant_id == tenant_id).count()
        if existing_count >= settings.demo_max_documents:
            raise HTTPException(
                403,
                f"Demo tenant document limit reached ({settings.demo_max_documents} max). "
                "This is a shared public demo tenant cap, not a bug.",
            )

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > max_mb:
        raise HTTPException(413, f"File exceeds {max_mb}MB limit{' (demo tenant cap)' if tenant.is_demo else ''}")

    doc = Document(
        tenant_id=tenant_id,
        source_id=source_id,
        filename=file.filename,
        mime_type=file.content_type,
        size_bytes=len(file_bytes),
        storage_path="",  # set below
        status=DocumentStatus.pending,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    storage_path = upload_file(tenant_id, doc.id, file.filename, file_bytes)
    doc.storage_path = storage_path
    db.commit()

    if settings.ingestion_mode == "sync":
        # Phase 1: process inline, request blocks until done.
        # Fine for small docs; for large files this is where you'd feel the
        # need for Phase 1.5 (QStash) - the request would just time out.
        try:
            process_document(db, doc.id, file_bytes)
        except Exception as e:
            return IngestResponse(document_id=doc.id, status="failed", message=str(e))
        return IngestResponse(document_id=doc.id, status="ready", message="Document processed successfully")

    elif settings.ingestion_mode == "qstash":
        # Phase 1.5 hook point (not implemented yet): enqueue a QStash
        # message pointing at POST /webhooks/ingest with {document_id}.
        # process_document() itself doesn't change at all.
        raise HTTPException(501, "qstash ingestion mode not implemented yet")

    raise HTTPException(500, f"Unknown ingestion_mode: {settings.ingestion_mode}")


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: str,
    tenant_id: str = Depends(require_tenant),
    db: Session = Depends(get_db),
):
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.tenant_id == tenant_id)
        .first()
    )
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(
    tenant_id: str = Depends(require_tenant),
    db: Session = Depends(get_db),
):
    return db.query(Document).filter(Document.tenant_id == tenant_id).order_by(Document.created_at.desc()).all()
