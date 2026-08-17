"""
Central config. All values come from environment variables so the same
code works locally (.env), on Render/Railway, and in CI.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Postgres / Supabase ---
    database_url: str  # postgresql+psycopg2://user:pass@host:port/dbname
    supabase_url: str = ""
    supabase_service_key: str = ""

    # --- Milvus / Zilliz Cloud ---
    milvus_uri: str  # e.g. https://xxx.api.gcp-us-west1.zillizcloud.com
    milvus_token: str  # Zilliz API token (or "user:password" for self-hosted)
    milvus_collection: str = "wizz_chunks"

    # --- Embeddings ---
    # Local, open-source, free by default (sentence-transformers, CPU) - the
    # only embedding path this project is built and tested against.
    # An OpenAI path exists in code for completeness but isn't the
    # recommended path for this project - keeping everything local/open-source
    # is a deliberate choice, not a fallback for lack of budget.
    embedding_provider: str = "local"
    embedding_model_local: str = "BAAI/bge-small-en-v1.5"  # 384 dims
    embedding_model_openai: str = "text-embedding-3-small"  # 1536 dims, not the default path
    openai_api_key: str = ""

    # --- LLM (generation) ---
    llm_provider: str = "openai"  # "openai" | "groq" (OpenAI-compatible API, different base_url)
    llm_model: str = "gpt-4o-mini"
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # --- Retrieval / reranking ---
    retrieval_mode: str = "hybrid"     # "hybrid" (dense+BM25, recommended) | "dense" (fallback/comparison)
    retrieval_top_k: int = 10          # candidates pulled from Milvus before reranking
    rerank_top_k: int = 4              # candidates kept after reranking, fed to the LLM
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"  # ONNX via fastembed, no torch

    # --- Ingestion behavior ---
    chunk_size: int = 400
    chunk_overlap: int = 60
    max_upload_mb: int = 20
    chunking_strategy: str = "auto"  # "auto" (recommended) | "recursive" | "semantic" (manual overrides for testing/debugging)
    semantic_chunk_breakpoint_percentile: float = 90.0

    # --- Document parsing ---
    document_parser: str = "basic"  # "basic" (pypdf/docx, fast/light) | "docling" (structure-aware, tables, OCR)
    caption_figures: bool = False   # opt-in - costs one vision LLM call per figure, only used with document_parser=docling
    max_figures_per_document: int = 10
    vision_model: str = "gpt-4o-mini"

    # --- Async trigger (Phase 1 -> Phase 1.5) ---
    # "sync": ingestion runs inline in the request (Phase 1, what we build now)
    # "qstash": endpoint enqueues to QStash which calls /webhooks/ingest later
    ingestion_mode: str = "sync"
    qstash_token: str = ""
    qstash_current_signing_key: str = ""
    qstash_next_signing_key: str = ""
    public_base_url: str = ""  # needed so QStash knows where to call back

    # --- Public demo (recruiter-facing) ---
    demo_enabled: bool = True
    demo_signups_per_ip_per_day: int = 3        # abuse guard on the signup endpoint itself
    demo_max_documents: int = 3                 # per demo tenant, lifetime
    demo_max_upload_mb: int = 2                 # smaller cap than the normal max_upload_mb
    demo_max_chat_per_hour: int = 20             # per demo tenant
    demo_max_chat_per_day: int = 60              # per demo tenant, secondary ceiling
    demo_tenant_ttl_days: int = 7                # for the cleanup script - not auto-enforced at request time

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def embedding_dim(self) -> int:
        """
        Must match the vector field dimension in the Milvus collection schema.
        If you change embedding_provider/model after the collection is created,
        you must recreate the collection - dims can't be changed in place.
        """
        return 384 if self.embedding_provider == "local" else 1536


@lru_cache
def get_settings() -> Settings:
    return Settings()
