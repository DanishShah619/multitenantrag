"""
Streams tokens as they're generated (for SSE), and separately returns a
structured citations list once generation finishes - so the frontend can
render the answer live, then attach source references at the end.
"""
from functools import lru_cache

from app.config import get_settings

settings = get_settings()

SYSTEM_PROMPT = """You are a helpful assistant answering questions using ONLY the provided context.
Rules:
- If the answer isn't in the context, say so clearly - do not make anything up.
- Cite which source chunk(s) you used by their [number] after each claim.
- Be concise and direct.
"""


@lru_cache
def _get_client():
    from openai import OpenAI
    if settings.llm_provider == "groq":
        return OpenAI(api_key=settings.groq_api_key, base_url=settings.groq_base_url)
    return OpenAI(api_key=settings.openai_api_key)


def _active_model() -> str:
    return settings.groq_model if settings.llm_provider == "groq" else settings.llm_model


def build_context(chunks: list[dict]) -> str:
    return "\n\n".join(f"[{i}] {c['text']}" for i, c in enumerate(chunks, start=1))


def build_citations(chunks: list[dict], doc_lookup: dict[str, str] | None = None) -> list[dict]:
    """
    doc_lookup: optional {document_id: filename} map, so citations show a
    real filename instead of a bare UUID. Caller fetches this from Postgres
    (see chat router) since Milvus itself doesn't store filenames.
    """
    doc_lookup = doc_lookup or {}
    citations = []
    for i, c in enumerate(chunks, start=1):
        citations.append({
            "ref": i,
            "document_id": c["document_id"],
            "filename": doc_lookup.get(c["document_id"], c["document_id"]),
            "content_type": c.get("content_type", "text"),
            "text_preview": c["text"][:160],
            "score": c.get("rerank_score", c.get("score")),
        })
    return citations


def stream_answer(query: str, chunks: list[dict]):
    """
    Generator yielding text deltas as they arrive from the LLM - the chat
    router wraps this in SSE 'data: ...' framing. Kept provider-agnostic
    at the call site so swapping models later doesn't touch the router.
    """
    client = _get_client()
    context = build_context(chunks)

    user_prompt = f"""Context:
{context}

Question: {query}

Answer using only the context above, citing sources by [number]."""

    stream = client.chat.completions.create(
        model=_active_model(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        stream=True,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
