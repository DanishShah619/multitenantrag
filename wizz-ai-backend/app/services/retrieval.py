from app.config import get_settings
from app.services.embedding import embed_query
from app.milvus_client import search as milvus_dense_search, hybrid_search as milvus_hybrid_search

settings = get_settings()


def retrieve(tenant_id: str, query: str, top_k: int | None = None) -> list[dict]:
    """
    Thin wrapper so the router doesn't touch milvus_client or embedding
    directly - keeps the tenant_id enforcement point in one obvious place.

    Dispatches on settings.retrieval_mode:
      "hybrid" (default) - dense + BM25 keyword search, fused via RRF.
        Catches exact-term queries (IDs, names, codes) that dense search
        alone tends to blur past.
      "dense" - vector search only. Kept for comparison in eval runs and
        as a fallback if the BM25 Function isn't available on a given
        Milvus deployment/tier.
    """
    k = top_k or settings.retrieval_top_k
    query_embedding = embed_query(query)

    if settings.retrieval_mode == "hybrid":
        return milvus_hybrid_search(tenant_id, query, query_embedding, top_k=k)

    return milvus_dense_search(tenant_id, query_embedding, top_k=k)
