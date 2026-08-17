"""
Rewritten on pymilvus's MilvusClient interface (not the older ORM-style
Collection API) specifically because Milvus's native BM25 Function support
- confirmed working against the real Zilliz Cloud free-tier cluster before
this was written - is exposed through this API. Mixing the old ORM API
with the new Function feature risks exactly the kind of version-mismatch
issues hit repeatedly during earlier setup, so this file is one consistent
API surface end to end.

Hybrid search = dense (semantic) + sparse (BM25 keyword) search run in
parallel, fused with Reciprocal Rank Fusion (RRF). This catches queries
dense search alone tends to miss - exact terms, product codes, street
names, IDs - things embedding similarity blurs but keyword matching
catches directly.

Tenant isolation: the tenant_id filter is applied to BOTH the dense and
sparse legs of every hybrid search, not just one - defense in depth, same
principle as the rest of this project.
"""
from pymilvus import (
    MilvusClient, DataType, Function, FunctionType,
    AnnSearchRequest, RRFRanker,
)

from app.config import get_settings

settings = get_settings()

_client: MilvusClient | None = None


def get_client() -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)
    return _client


def get_or_create_collection():
    """
    Idempotent - safe to call on every request. Creates the collection with
    both a dense vector field (your embedding model's output) and a sparse
    vector field auto-populated by Milvus's built-in BM25 Function from the
    'text' field - no separate keyword index to build or maintain yourself.
    """
    client = get_client()
    name = settings.milvus_collection

    if client.has_collection(name):
        return client

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, max_length=64, is_primary=True)
    schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
    schema.add_field("document_id", DataType.VARCHAR, max_length=64)
    schema.add_field("source_id", DataType.VARCHAR, max_length=64)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("content_type", DataType.VARCHAR, max_length=24)  # "text" | "table" | "table_low_confidence" | "figure_caption"
    # enable_analyzer=True is required for the BM25 function to tokenize this field
    schema.add_field("text", DataType.VARCHAR, max_length=8000, enable_analyzer=True)
    schema.add_field("text_sparse", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=settings.embedding_dim)

    bm25_function = Function(
        name="text_bm25",
        input_field_names=["text"],
        output_field_names=["text_sparse"],
        function_type=FunctionType.BM25,
    )
    schema.add_function(bm25_function)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="text_sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25"
    )
    index_params.add_index(
        field_name="embedding", index_type="HNSW", metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )

    client.create_collection(collection_name=name, schema=schema, index_params=index_params)
    client.load_collection(name)
    return client


def upsert_chunks(tenant_id: str, document_id: str, source_id: str, chunks: list[dict]):
    """
    chunks: list of {id, chunk_index, text, embedding, content_type (optional)}
    Note: text_sparse is NOT provided here - Milvus computes it automatically
    from the 'text' field via the BM25 Function at insert time.
    """
    get_or_create_collection()
    client = get_client()

    rows = [
        {
            "id": c["id"],
            "tenant_id": tenant_id,
            "document_id": document_id,
            "source_id": source_id or "",
            "chunk_index": c["chunk_index"],
            "content_type": c.get("content_type", "text"),
            "text": c["text"],
            "embedding": c["embedding"],
        }
        for c in chunks
    ]
    client.insert(collection_name=settings.milvus_collection, data=rows)


def delete_document_chunks(tenant_id: str, document_id: str):
    get_or_create_collection()
    client = get_client()
    client.delete(
        collection_name=settings.milvus_collection,
        filter=f'tenant_id == "{tenant_id}" && document_id == "{document_id}"',
    )


def search(tenant_id: str, query_embedding: list[float], top_k: int = 5) -> list[dict]:
    """
    Dense-only search - kept for comparison/fallback and for the eval
    harness to measure dense-alone vs hybrid as separate conditions.
    """
    get_or_create_collection()
    client = get_client()

    results = client.search(
        collection_name=settings.milvus_collection,
        data=[query_embedding],
        anns_field="embedding",
        search_params={"metric_type": "COSINE", "params": {"ef": 64}},
        limit=top_k,
        filter=f'tenant_id == "{tenant_id}"',
        output_fields=["text", "document_id", "source_id", "chunk_index", "content_type"],
    )
    return _format_hits(results[0])


def hybrid_search(
    tenant_id: str,
    query_text: str,
    query_embedding: list[float],
    top_k: int = 10,
) -> list[dict]:
    """
    Runs dense (semantic) and sparse (BM25 keyword) search in parallel,
    fused with RRF. tenant_id filter is applied on BOTH legs independently -
    never rely on only one leg being scoped correctly.
    """
    get_or_create_collection()
    client = get_client()

    tenant_filter = f'tenant_id == "{tenant_id}"'

    dense_req = AnnSearchRequest(
        data=[query_embedding],
        anns_field="embedding",
        param={"metric_type": "COSINE", "params": {"ef": 64}},
        limit=top_k,
        expr=tenant_filter,
    )
    sparse_req = AnnSearchRequest(
        data=[query_text],  # raw text - Milvus tokenizes via the BM25 function server-side
        anns_field="text_sparse",
        param={"metric_type": "BM25"},
        limit=top_k,
        expr=tenant_filter,
    )

    results = client.hybrid_search(
        collection_name=settings.milvus_collection,
        reqs=[dense_req, sparse_req],
        ranker=RRFRanker(),  # Reciprocal Rank Fusion - no manual score weighting to tune
        limit=top_k,
        output_fields=["text", "document_id", "source_id", "chunk_index", "content_type"],
    )
    return _format_hits(results[0])


def _format_hits(hits) -> list[dict]:
    out = []
    for hit in hits:
        entity = hit.get("entity", hit)  # MilvusClient result shape
        out.append({
            "text": entity.get("text"),
            "document_id": entity.get("document_id"),
            "source_id": entity.get("source_id"),
            "chunk_index": entity.get("chunk_index"),
            "content_type": entity.get("content_type"),
            "score": hit.get("distance", hit.get("score")),
        })
    return out
