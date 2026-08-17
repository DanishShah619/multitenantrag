"""
This is the single function that does the actual ingestion work:
parse -> chunk -> embed -> upsert to Milvus -> update Postgres status.

Deliberately framework-agnostic: it doesn't know or care whether it was
called directly from a FastAPI route (Phase 1, sync) or from a QStash
webhook handler (Phase 1.5, async). That's the whole point of keeping the
trigger mechanism separate from the pipeline logic - swapping sync for
async later is a routing change, not a rewrite.
"""
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Document, DocumentStatus
from app.services.chunking import chunk_text, semantic_chunk_text, choose_strategy
from app.services.embedding import embed_texts
from app.milvus_client import upsert_chunks, delete_document_chunks
from app.config import get_settings

settings = get_settings()


def extract_text(file_bytes: bytes, mime_type: str, filename: str) -> str:
    """
    Basic, fast, free extraction path - flat text only, no tables/figures/OCR.
    Used when document_parser="basic" (the default). For structure-aware
    parsing with table and figure support, see document_parsing.py and
    document_parser="docling" below.
    """
    if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)

    elif mime_type in ("text/plain", "text/markdown") or filename.lower().endswith((".txt", ".md")):
        return file_bytes.decode("utf-8", errors="ignore")

    elif filename.lower().endswith(".docx"):
        import docx
        import io
        doc = docx.Document(io.BytesIO(file_bytes))
        return "\n\n".join(p.text for p in doc.paragraphs)

    else:
        raise ValueError(f"Unsupported file type: {mime_type or filename}")


def get_chunks(file_bytes: bytes, mime_type: str, filename: str) -> list[dict]:
    """
    Returns a unified list of {text, content_type} regardless of which
    parsing path ran, so process_document() doesn't need to know or care
    which strategy produced them.
    """
    if settings.document_parser == "docling":
        from app.services.document_parsing import (
            parse_with_docling, chunk_docling_document, extract_figures, caption_figure
        )

        doc = parse_with_docling(file_bytes, filename)
        chunks = chunk_docling_document(doc)  # [{text, content_type}], tables kept intact

        if settings.caption_figures:
            figures = extract_figures(doc, max_figures=settings.max_figures_per_document)
            for fig in figures:
                try:
                    caption = caption_figure(fig["image_bytes"])
                    chunks.append({"text": caption, "content_type": "figure_caption"})
                except Exception:
                    continue  # one bad figure shouldn't fail the whole ingestion

        return chunks

    # basic path
    if filename.lower().endswith(".pdf") or mime_type == "application/pdf":
        from app.services.table_extraction import extract_structured_pdf
        blocks = extract_structured_pdf(file_bytes)
    elif filename.lower().endswith(".docx"):
        from app.services.table_extraction import extract_structured_docx
        blocks = extract_structured_docx(file_bytes)
    else:
        # .txt/.md and anything else without a structured extractor - no tables to speak of
        text = extract_text(file_bytes, mime_type, filename)
        blocks = [{"type": "text", "content": text}] if text.strip() else []

    if not blocks:
        raise ValueError("No extractable content found in document")

    result_chunks = []

    # concatenate all text blocks so the chunking strategy heuristic sees the
    # document's full shape, not one page/section at a time
    text_blocks = [b["content"] for b in blocks if b["type"] == "text"]
    if text_blocks:
        full_text = "\n\n".join(text_blocks)
        strategy = settings.chunking_strategy
        if strategy == "auto":
            strategy = choose_strategy(full_text, settings.embedding_provider)
        raw_chunks = (
            semantic_chunk_text(full_text, settings.semantic_chunk_breakpoint_percentile)
            if strategy == "semantic"
            else chunk_text(full_text)
        )
        result_chunks.extend(
            {"text": c, "content_type": "text", "_chunking_strategy_used": strategy} for c in raw_chunks
        )

    # tables are kept as their own chunks, never merged into the text
    # chunking pass - splitting a table mid-row destroys its meaning.
    # Tables flagged as possibly missing their row labels get a distinct
    # content_type so they can be filtered from retrieval or shown with a
    # lower-confidence indicator in citations, rather than silently cited
    # as if they were reliable.
    for b in blocks:
        if b["type"] == "table":
            content_type = "table_low_confidence" if b.get("possibly_orphaned") else "table"
            result_chunks.append({"text": b["content"], "content_type": content_type})

    return result_chunks


def process_document(db: Session, document_id: str, file_bytes: bytes) -> None:
    """
    Mutates the Document row's status as it goes, so the frontend can poll
    GET /documents/{id} and show progress. Any exception marks the doc
    'failed' with the error message attached, rather than leaving it stuck
    on 'processing' forever.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise ValueError(f"Document {document_id} not found")

    try:
        doc.status = DocumentStatus.processing
        db.commit()

        chunks = get_chunks(file_bytes, doc.mime_type, doc.filename)
        if not chunks:
            raise ValueError("Parsing produced zero valid chunks")

        strategy_used = chunks[0].get("_chunking_strategy_used", "n/a") if chunks else "n/a"
        print(f"[ingestion] document_id={doc.id} chunking_strategy_used={strategy_used} chunk_count={len(chunks)}")

        texts = [c["text"] for c in chunks]
        embeddings = embed_texts(texts)

        # If this doc was previously ingested (re-upload), clear old vectors
        # first so we don't leave orphaned/duplicate chunks in Milvus.
        delete_document_chunks(tenant_id=doc.tenant_id, document_id=doc.id)

        milvus_records = [
            {
                "id": str(uuid.uuid4()),
                "chunk_index": i,
                "text": c["text"],
                "content_type": c.get("content_type", "text"),
                "embedding": embeddings[i],
            }
            for i, c in enumerate(chunks)
        ]
        upsert_chunks(
            tenant_id=doc.tenant_id,
            document_id=doc.id,
            source_id=doc.source_id,
            chunks=milvus_records,
        )

        doc.status = DocumentStatus.ready
        doc.chunk_count = len(chunks)
        doc.processed_at = datetime.utcnow()
        doc.error_message = None
        db.commit()

    except Exception as e:
        db.rollback()
        doc.status = DocumentStatus.failed
        doc.error_message = str(e)[:2000]
        db.commit()
        raise
