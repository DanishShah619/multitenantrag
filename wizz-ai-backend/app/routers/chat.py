import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_embed_scope
from app.database import get_db
from app.config import get_settings
from app.models import Chat, Message, Document, Tenant
from app.services.retrieval import retrieve
from app.services.reranking import rerank
from app.services.generation import stream_answer, build_citations
from app.rate_limit import is_allowed

router = APIRouter(prefix="/chat", tags=["chat"])
settings = get_settings()


class ChatRequest(BaseModel):
    query: str
    chat_id: str | None = None  # omit to start a new chat thread
    visitor_id: str | None = None


def _doc_filename_lookup(db: Session, tenant_id: str, chunks: list[dict]) -> dict[str, str]:
    doc_ids = list({c["document_id"] for c in chunks})
    if not doc_ids:
        return {}
    rows = (
        db.query(Document.id, Document.filename)
        .filter(Document.tenant_id == tenant_id, Document.id.in_(doc_ids))
        .all()
    )
    return {row.id: row.filename for row in rows}


@router.post("")
def chat(
    payload: ChatRequest,
    tenant_id: str = Depends(require_embed_scope),
    db: Session = Depends(get_db),
):
    if not payload.query.strip():
        raise HTTPException(400, "query cannot be empty")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    # Demo tenants get hourly + daily message caps - two windows rather
    # than one, so a burst doesn't quietly exhaust the whole day's budget
    # in a minute, and a slow trickle doesn't quietly exceed a sane daily
    # total either.
    if tenant.is_demo:
        if not is_allowed(f"demo_chat_hour:{tenant_id}", settings.demo_max_chat_per_hour, window_seconds=3600):
            raise HTTPException(
                429,
                f"Demo tenant hourly chat limit reached ({settings.demo_max_chat_per_hour}/hour). "
                "This is a shared public demo cap - please try again shortly.",
            )
        if not is_allowed(f"demo_chat_day:{tenant_id}", settings.demo_max_chat_per_day, window_seconds=86400):
            raise HTTPException(
                429,
                f"Demo tenant daily chat limit reached ({settings.demo_max_chat_per_day}/day).",
            )

    # --- get or create the chat thread (scoped to tenant, same as everything else) ---
    if payload.chat_id:
        chat_row = (
            db.query(Chat)
            .filter(Chat.id == payload.chat_id, Chat.tenant_id == tenant_id)
            .first()
        )
        if not chat_row:
            raise HTTPException(404, "Chat not found")
    else:
        chat_row = Chat(tenant_id=tenant_id, visitor_id=payload.visitor_id)
        db.add(chat_row)
        db.commit()
        db.refresh(chat_row)

    user_msg = Message(chat_id=chat_row.id, role="user", content=payload.query)
    db.add(user_msg)
    db.commit()

    # --- retrieve -> rerank (blocking, fast enough to do before streaming starts) ---
    candidates = retrieve(tenant_id, payload.query)
    top_chunks = rerank(payload.query, candidates)

    if not top_chunks:
        # Nothing relevant found at all - don't call the LLM, just say so.
        def empty_stream():
            msg = "I don't have any information about that in the connected documents."
            yield f"data: {json.dumps({'type': 'token', 'content': msg})}\n\n"
            yield f"data: {json.dumps({'type': 'citations', 'citations': []})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'chat_id': chat_row.id})}\n\n"

            assistant_msg = Message(chat_id=chat_row.id, role="assistant", content=msg, citations="[]")
            db.add(assistant_msg)
            db.commit()

        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    doc_lookup = _doc_filename_lookup(db, tenant_id, top_chunks)
    citations = build_citations(top_chunks, doc_lookup)

    def event_stream():
        full_answer = ""
        try:
            for delta in stream_answer(payload.query, top_chunks):
                full_answer += delta
                yield f"data: {json.dumps({'type': 'token', 'content': delta})}\n\n"

            yield f"data: {json.dumps({'type': 'citations', 'citations': citations})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'chat_id': chat_row.id})}\n\n"

        finally:
            # Persist even if the client disconnects mid-stream, so history
            # isn't silently lost on a dropped connection.
            assistant_msg = Message(
                chat_id=chat_row.id,
                role="assistant",
                content=full_answer,
                citations=json.dumps(citations),
            )
            db.add(assistant_msg)
            db.commit()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{chat_id}/history")
def chat_history(
    chat_id: str,
    tenant_id: str = Depends(require_embed_scope),
    db: Session = Depends(get_db),
):
    chat_row = db.query(Chat).filter(Chat.id == chat_id, Chat.tenant_id == tenant_id).first()
    if not chat_row:
        raise HTTPException(404, "Chat not found")

    return [
        {
            "role": m.role,
            "content": m.content,
            "citations": json.loads(m.citations) if m.citations else [],
            "created_at": m.created_at.isoformat(),
        }
        for m in sorted(chat_row.messages, key=lambda m: m.created_at)
    ]
