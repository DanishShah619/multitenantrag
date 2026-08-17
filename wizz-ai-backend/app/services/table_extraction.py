"""
Lightweight table extraction for the "basic" document_parser path - no
layout-detection models, no OCR, no torch.

DOCX: python-docx exposes table structure directly through its own API,
used as-is - validated clean on a real multi-table test file.

PDF: pdf_inspector (Rust-native, no ML models) is used instead of
pdfplumber. Both were tested against a real, complex 46-page report
(IMF World Economic Outlook) - pdfplumber's bbox-based heuristic table
detection produced garbled, misaligned tables with row labels dropped and
cell contents bleeding across columns on multi-column financial tables.
pdf_inspector performed meaningfully better on the same document (clean
tables on most pages, correct heading structure, working OCR-page
detection) though it is NOT perfect either - see _looks_like_orphaned_data
below. This is evidence-based, not a default preference: both tools use
a similar class of heuristic (geometric/whitespace-based region detection)
and hit the same wall on some complex layouts; pdf_inspector simply hit it
less often on the document actually tested.

This is deliberately NOT a Docling replacement - no OCR, no figure/chart
handling. It closes the specific "tables get silently dropped/mangled" gap
on infrastructure that can't afford Docling's memory footprint (see README
for the full tradeoff writeup).
"""
import re


def table_to_markdown(rows: list[list]) -> str:
    """Renders a list-of-rows table as a markdown table string."""
    if not rows:
        return ""
    header = [str(c) if c is not None else "" for c in rows[0]]
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in rows[1:]:
        cells = [str(c) if c is not None else "" for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _looks_like_orphaned_data(table_markdown: str) -> bool:
    """
    Flags tables that are very likely missing their row labels - a real,
    observed failure mode on complex multi-column layouts (confirmed on
    both pdfplumber and pdf_inspector against the same real document).
    A table with almost no alphabetic content is a strong signal the label
    column got dropped or misaligned during extraction, leaving orphaned
    numbers with no identifiable row meaning.

    This doesn't fix the underlying extraction gap - it flags it, so a
    near-useless chunk (a wall of numbers with no label) can be excluded
    or down-weighted rather than silently ingested and confidently cited
    as if it were reliable data.
    """
    letters = sum(c.isalpha() for c in table_markdown)
    total_chars = max(len(table_markdown), 1)
    return (letters / total_chars) < 0.05  # heuristic threshold - tune against real corpus, not guessed once


def extract_structured_docx(file_bytes: bytes) -> list[dict]:
    """
    Iterates the document body in actual document order (paragraphs and
    tables interleaved as they appear), not paragraphs-then-tables - so a
    table stays attached to the section it belongs to, not dumped at the
    end disconnected from its context.
    """
    import io
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    document = docx.Document(io.BytesIO(file_bytes))

    def iter_block_items(doc):
        for child in doc.element.body.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, doc)
            elif isinstance(child, CT_Tbl):
                yield Table(child, doc)

    blocks = []
    text_buffer = []

    def flush_text():
        if text_buffer:
            joined = "\n\n".join(t for t in text_buffer if t.strip())
            if joined.strip():
                blocks.append({"type": "text", "content": joined})
            text_buffer.clear()

    for item in iter_block_items(document):
        if isinstance(item, Paragraph):
            if item.text.strip():
                text_buffer.append(item.text)
        elif isinstance(item, Table):
            flush_text()
            rows = [[cell.text for cell in row.cells] for row in item.rows]
            md = table_to_markdown(rows)
            if md.strip():
                blocks.append({
                    "type": "table",
                    "content": md,
                    "possibly_orphaned": _looks_like_orphaned_data(md),
                })

    flush_text()
    return blocks


def extract_structured_pdf(file_bytes: bytes) -> list[dict]:
    """
    Uses pdf_inspector (Rust-native) for PDF text/markdown/table extraction.
    Returns one combined markdown document, which is split here into
    contiguous text vs. table blocks (lines starting with '|' are treated
    as table regions), tagged with content_type the same way the DOCX path
    is, so downstream code doesn't need to know which parser produced them.

    Also surfaces pages flagged as needing OCR - not acted on yet (no OCR
    pipeline wired in), but logged rather than silently lost, since a
    scanned page currently means missing content with no visible signal
    why.
    """
    import tempfile
    import os
    import pdf_inspector

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = pdf_inspector.process_pdf(tmp_path)
    finally:
        os.unlink(tmp_path)

    if getattr(result, "pages_needing_ocr", None):
        print(f"[table_extraction] PDF has {len(result.pages_needing_ocr)} page(s) needing OCR "
              f"(not processed - document_parser='basic' has no OCR support): {result.pages_needing_ocr}")

    return _split_markdown_into_blocks(result.markdown)


def _split_markdown_into_blocks(markdown: str) -> list[dict]:
    """
    Splits pdf_inspector's combined markdown output into contiguous table
    regions (consecutive lines starting with '|') vs. text regions.
    """
    lines = markdown.split("\n")
    blocks = []
    current_type = None
    buffer = []

    def flush():
        if not buffer:
            return
        content = "\n".join(buffer).strip()
        if not content:
            return
        if current_type == "table":
            blocks.append({
                "type": "table",
                "content": content,
                "possibly_orphaned": _looks_like_orphaned_data(content),
            })
        else:
            blocks.append({"type": "text", "content": content})

    for line in lines:
        is_table_line = line.strip().startswith("|")
        line_type = "table" if is_table_line else "text"

        if current_type is not None and line_type != current_type:
            flush()
            buffer = []

        current_type = line_type
        buffer.append(line)

    flush()
    return blocks
