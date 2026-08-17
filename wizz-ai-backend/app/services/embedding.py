"""
Two providers behind one interface:
  - "local": fastembed (ONNX Runtime), CPU, zero API cost, no torch
    dependency. Deliberately NOT sentence-transformers - that pulls in
    full PyTorch, which risks exceeding Render's 512MB free-tier RAM
    before the app serves a single request (the same reasoning that led
    to choosing fastembed over sentence-transformers' CrossEncoder for
    reranking). Same model (BAAI/bge-small-en-v1.5), same 384 dims,
    lighter runtime.
  - "openai"/"groq"-compatible: not used for embeddings currently (Groq
    doesn't offer an embeddings endpoint) - "openai" path kept for
    completeness, not the recommended path for this project.

Swap via EMBEDDING_PROVIDER env var. Whichever you pick, embedding_dim in
config.py must match, and the Milvus collection must be created with that
dim - changing providers after ingesting data requires re-embedding
everything into a fresh collection.
"""
from functools import lru_cache

from app.config import get_settings

settings = get_settings()


@lru_cache
def _get_local_model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=settings.embedding_model_local)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    if settings.embedding_provider == "local":
        model = _get_local_model()
        # fastembed returns a generator of numpy arrays, not a list - list()
        # forces evaluation so callers get a plain list like before
        vectors = list(model.embed(texts))
        return [v.tolist() for v in vectors]

    elif settings.embedding_provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)
        resp = client.embeddings.create(model=settings.embedding_model_openai, input=texts)
        return [item.embedding for item in resp.data]

    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
