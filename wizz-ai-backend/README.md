# Wizz AI Backend — Phase 1 (Ingestion Pipeline)

Synchronous document ingestion: upload → chunk → embed → store in Milvus,
with tenant isolation enforced at every layer.

## Setup

1. **Zilliz Cloud (free Milvus)**: create a free cluster at
   https://zilliz.com/cloud, copy the URI + token into `.env`.
2. **Supabase**: create a project, run the schema (auto-created on first
   startup via `Base.metadata.create_all`), create a storage bucket named
   `tenant-documents`, copy `DATABASE_URL` (use the pooler connection
   string, port 6543, for serverless-friendly connections) and
   `SUPABASE_SERVICE_KEY`.
3. Copy `.env.example` to `.env` and fill in the values.
4. Install deps:
   ```bash
   pip install -r requirements.txt
   ```
5. Create a test tenant + API key:
   ```bash
   python -m scripts.create_tenant "Acme Corp"
   ```
   Save the printed API key — it's shown once.
6. Run the server:
   ```bash
   uvicorn app.main:app --reload
   ```

## Try it

```bash
curl -X POST http://localhost:8000/documents \
  -H "X-API-Key: <your-key-from-step-5>" \
  -F "file=@/path/to/some.pdf"
```

Check status:
```bash
curl http://localhost:8000/documents/<document_id> \
  -H "X-API-Key: <your-key-from-step-5>"
```

## Public demo endpoint (for recruiters / quick evaluation)

`POST /demo/signup` is unauthenticated and self-service - anyone can call
it to get a working, quota-limited tenant with zero setup:

```bash
curl -X POST https://your-render-url.onrender.com/demo/signup \
  -H "Content-Type: application/json" \
  -d '{"label": "recruiter test"}'
```

Returns a `tenant_id`, an `admin_api_key` (for `/documents` uploads) and
an `embed_api_key` (for `/chat`), plus the limits in effect. From there:

```bash
curl -X POST https://your-render-url.onrender.com/documents \
  -H "X-API-Key: <admin_api_key>" \
  -F "file=@sample.pdf"

curl -X POST https://your-render-url.onrender.com/chat \
  -H "X-API-Key: <embed_api_key>" \
  -H "Content-Type: application/json" \
  -d '{"query": "what does this document say about X?"}'
```

**Guardrails in effect** (all in `app/config.py`, tune via env vars):
- Signup itself is IP-rate-limited (`DEMO_SIGNUPS_PER_IP_PER_DAY`, default 3/day)
- Each demo tenant is capped at `DEMO_MAX_DOCUMENTS` documents (default 3),
  each under `DEMO_MAX_UPLOAD_MB` (default 2MB)
- Chat is capped at `DEMO_MAX_CHAT_PER_HOUR` and `DEMO_MAX_CHAT_PER_DAY`
  (defaults 20/hour, 60/day) per tenant
- Demo tenants are intended to be temporary - `scripts/cleanup_demo_tenants.py`
  deletes tenants older than `DEMO_TENANT_TTL_DAYS` (default 7), but this is
  **not scheduled automatically** - run it manually or wire it into a cron
  job / GitHub Action if you want automatic expiry

**Known limitation**: rate limiting (`app/rate_limit.py`) is in-memory,
per-process. This is fine for a single Render free-tier instance (the
normal case here) but does NOT share state across multiple instances - if
you ever scale this service horizontally, each instance enforces its own
independent limit, effectively multiplying the real cap by instance count.
Swap in a Redis/Upstash-backed limiter (same function signature) if that
ever matters.

## Running evals

Before tuning retrieval further (hybrid search, different chunk sizes, a
bigger reranker model), measure the current baseline so you can tell if a
change actually helps:

```bash
# retrieval + rerank metrics only, no LLM calls, free
python -m eval.run_eval --tenant-id <your-tenant-id> --no-generation

# full eval including generation quality + refusal accuracy (costs LLM calls)
python -m eval.run_eval --tenant-id <your-tenant-id>
```

Reports:
- **Retrieval / Rerank Hit Rate@k** — did the expected content make it into
  the candidate set the LLM actually sees
- **MRR** — how close to the top the relevant chunk landed, post-rerank
- **Citation Rate** — did the answer actually cite a source
- **Groundedness (heuristic)** — crude keyword-overlap check; spot-check
  flagged answers manually, this isn't a substitute for reading them
- **Refusal Accuracy** — for genuinely unanswerable queries, did the system
  correctly say so instead of fabricating an answer

`eval/dataset.json` ships with example queries matched to the civic-complaint
FAQ content used in the Colab validation notebook. Update it to match
whatever documents you actually ingest — the eval is only meaningful against
real ingested content for that tenant.

## What's deliberately NOT here yet

- **Retrieval/chat endpoint** (Phase 2) — the `search()` function in
  `milvus_client.py` is ready to be called from a chat router.
- **Async ingestion via QStash** (Phase 1.5) — `ingestion_mode=sync` runs
  inline. `process_document()` in `services/ingestion.py` is written to be
  trigger-agnostic, so adding a `/webhooks/ingest` route that calls the same
  function is a small addition, not a rewrite.
- **Widget embed script / Next.js dashboard** (Phase 3).
- **Alembic migrations** — currently using `create_all` for speed; swap in
  before this touches a real schema you care about preserving.
- **Hybrid search (BM25 + dense)** — currently pure dense vector search.
  Worth adding after you have eval numbers to compare against.
- **HyDE query rewriting** — deliberately skipped; adds an LLM call and
  latency before retrieval even starts, with mixed benefit on well-formed
  queries. Revisit only if evals show short/vague queries underperforming.

## Document parsing: basic (with lightweight tables) vs docling

Two ingestion paths, toggled by `DOCUMENT_PARSER`:

- **`basic`** (default, Render-friendly) — `pypdf` fallback text, plus
  structured extraction: `pdf_inspector` (Rust-native, no ML models,
  ~5-6MB binary) for PDFs, `python-docx`'s own table API for DOCX. Tables
  are extracted as markdown and kept as their own chunks (`content_type`
  = `"table"`), never split mid-row. Chosen over `pdfplumber` after
  testing both against a real, complex 46-page report (IMF World Economic
  Outlook): `pdfplumber`'s bbox-based table detection produced garbled,
  misaligned tables with row labels dropped on multi-column financial
  tables; `pdf_inspector` performed meaningfully better on the same
  document (see git history / conversation log for the side-by-side
  evidence). **Neither tool is fully reliable on this table shape** - a
  `_looks_like_orphaned_data()` heuristic flags tables that are
  suspiciously low on alphabetic content (a strong signal the row-label
  column was dropped or misaligned), tagging them `content_type` =
  `"table_low_confidence"` instead of `"table"` so they can be filtered
  from retrieval or shown with a lower-confidence indicator, rather than
  silently cited as reliable data. `pdf_inspector` also flags pages
  needing OCR (`pages_needing_ocr`) - not acted on in the basic path (no
  OCR pipeline), but logged instead of silently dropped.
  **Scope limits**: no OCR, no figure/chart extraction.
- **`docling`** — full layout-aware parsing: OCR for scanned pages, figure
  extraction, more robust table structure recognition via a trained model
  rather than heuristics. **Not recommended for an always-on free-tier
  deployment** (Render free tier is 512MB RAM; Docling's layout/OCR models
  alone can exceed that before serving a single request, and the free tier's
  ephemeral disk means model weights may re-download on every cold start).
  Better suited to a local/offline ingestion script or a host with more
  headroom. A hosted Docling API exists (`docling-serve`, the project's own
  REST wrapper) but its container image is 4.4-8.7GB with models baked in -
  it relocates the resource requirement rather than removing it. Managed
  alternatives with real free tiers exist (LlamaParse, Unstructured.io) if
  you want structure-aware parsing without hosting it yourself.
  - `CAPTION_FIGURES=true` additionally sends each extracted figure to a
    vision LLM (`VISION_MODEL`, default `gpt-4o-mini`) to generate a text
    caption, which gets embedded and made retrievable like any other chunk.
    **This costs one LLM call per figure** — off by default, capped at
    `MAX_FIGURES_PER_DOCUMENT` (default 10) when enabled.

Every chunk carries a `content_type` field (`text` | `table` |
`figure_caption`) in Milvus, surfaced in chat citations so an answer can
indicate it came from a table or a chart description, not just prose.

**Note**: adding the `content_type` field to the Milvus schema means an
existing collection created before this change needs to be dropped and
recreated (`utility.drop_collection(...)`) — the schema isn't
migrated in place.

## Tenant isolation checklist (test this before adding features)

- [ ] `tenant_id` always comes from the API key dependency, never from
      request body/query params.
- [ ] Every Milvus `search()` and `delete()` call includes a `tenant_id`
      filter, even when the caller "should" already be scoped correctly.
- [ ] Storage paths are namespaced `{tenant_id}/{document_id}/...`.
- [ ] Every SQLAlchemy query on a tenant-scoped table filters by
      `tenant_id` explicitly (no relying on "we'll never forget").
