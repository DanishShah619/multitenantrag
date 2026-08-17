"""
Structure-aware document parsing via Docling - layout-aware extraction that
preserves headings, reading order, and table structure, with optional OCR
for scanned pages and optional vision-LLM captioning for figures/charts.

This is deliberately kept separate from the basic extract_text() path in
ingestion.py rather than replacing it outright:
  - Docling is a meaningfully heavier dependency (layout models, optional
    OCR backend) and slower per-document than plain text extraction.
  - Not every ingested document needs table/figure handling - a plain FAQ
    .txt file gets nothing from this and pays the cost for nothing.
  - Toggle via DOCUMENT_PARSER=docling vs the existing "basic" path, so
    free-tier / low-complexity ingestion isn't forced to pay the Docling
    cost by default.
"""
import tempfile
import os

from app.config import get_settings

settings = get_settings()


def parse_with_docling(file_bytes: bytes, filename: str) -> "DoclingDocument":
    """
    Docling's DocumentConverter works off a file path, so we write the
    upload to a temp file first rather than piping bytes directly.
    """
    from docling.document_converter import DocumentConverter

    suffix = os.path.splitext(filename)[1] or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        return result.document
    finally:
        os.unlink(tmp_path)


def chunk_docling_document(doc) -> list[dict]:
    """
    HybridChunker respects document structure - won't split a table in
    half, keeps headings attached to their section content. Returns dicts
    with a 'content_type' tag so downstream code (and Milvus metadata) can
    distinguish prose from table content, which matters for citation
    display ("this came from a table" vs "this came from body text").
    """
    from docling.chunking import HybridChunker

    chunker = HybridChunker()
    raw_chunks = list(chunker.chunk(doc))

    results = []
    for chunk in raw_chunks:
        text = chunker.serialize(chunk)
        if not text or len(text.strip()) < 10:
            continue

        # Docling tags chunk provenance - detect table content from the
        # chunk's doc_items when available, fall back to a text heuristic
        content_type = "text"
        items = getattr(chunk, "meta", None) and getattr(chunk.meta, "doc_items", None)
        if items and any(getattr(i, "label", "") == "table" for i in items):
            content_type = "table"

        results.append({"text": text.strip(), "content_type": content_type})

    return results


def extract_figures(doc, max_figures: int = 10) -> list[dict]:
    """
    Returns cropped figure images as PNG bytes, ready to hand to a vision
    LLM for captioning. Capped at max_figures per document since captioning
    costs one LLM call per image - an uncapped chart-heavy PDF could get
    expensive fast without a ceiling.
    """
    figures = []
    for i, picture in enumerate(doc.pictures):
        if i >= max_figures:
            break
        try:
            image = picture.get_image(doc)
            if image is None:
                continue
            import io
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            figures.append({"index": i, "image_bytes": buf.getvalue()})
        except Exception:
            continue  # skip figures Docling couldn't extract, don't fail the whole ingestion over one bad image
    return figures


def caption_figure(image_bytes: bytes) -> str:
    """
    Opt-in vision LLM call - costs money per image, so this is only invoked
    when settings.caption_figures is explicitly enabled, not by default.
    """
    import base64
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    b64 = base64.b64encode(image_bytes).decode()

    response = client.chat.completions.create(
        model=settings.vision_model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this chart/figure factually in 2-3 sentences. State the exact data shown (labels, values, trend) if it's a chart or graph. Do not speculate beyond what's visible."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        max_tokens=250,
    )
    return response.choices[0].message.content
