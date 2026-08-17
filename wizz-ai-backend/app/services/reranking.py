"""
fastembed's TextCrossEncoder runs via ONNX Runtime instead of PyTorch -
much lighter install, faster cold start, same underlying model quality
class as sentence-transformers' CrossEncoder.
"""
from functools import lru_cache

from app.config import get_settings

settings = get_settings()


@lru_cache
def _get_reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder(model_name=settings.reranker_model)


def rerank(query: str, candidates: list[dict], top_k: int | None = None) -> list[dict]:
    """
    candidates: output of retrieval.retrieve() - list of dicts with a 'text' key.
    Returns the same dicts, re-sorted, with an added 'rerank_score', truncated
    to top_k.
    """
    if not candidates:
        return []

    top_k = top_k or settings.rerank_top_k
    reranker = _get_reranker()

    texts = [c["text"] for c in candidates]
    scores = list(reranker.rerank(query, texts))

    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)

    return sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)[:top_k]
