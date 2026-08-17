from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings

settings = get_settings()


def choose_strategy(text: str, embedding_provider: str = "local") -> str:
    """
    Decides recursive vs semantic chunking from the document's own shape -
    nobody (not the developer, not a tenant) should have to manually pick
    a strategy per document. Pure structural heuristic on raw text (no
    embedding calls), so the decision itself costs nothing before we know
    which path is worth paying for.

    Reasoning:
      - A document that's already one continuous topic (a contract, a
        single long article) gains nothing from semantic boundary
        detection - paragraph-based recursive splitting does the same job
        for free. Only route to semantic when there's evidence of multiple
        short, distinct topics (FAQs, policy manuals, knowledge bases) -
        exactly the shape that broke naive fixed-size chunking earlier.
      - With embedding_provider="local" (the default here - free, CPU),
        semantic chunking's extra cost is just compute time, so we can
        default to it more freely. If a paid provider is ever used instead,
        be more conservative about spending a per-sentence embedding call
        on every ingested document.

    KNOWN FAILURE MODE (found via real testing, not guessed): markdown
    output from PDF parsers (e.g. pdf_inspector) mixes real prose
    paragraphs with headings, captions, footnote markers, and other short
    structural fragments - all separated by the same \\n\\n a real FAQ
    entry would use. Naively counting every \\n\\n-separated block as a
    "paragraph" makes a long, continuous analytical report LOOK
    FAQ-shaped (many short blocks) when it's actually dense, cohesive
    prose with a lot of structural noise around it - misrouting it to
    semantic chunking, which then over-fragments (600+ tiny chunks
    observed on a 46-page report in testing). Headings and very short
    fragments are filtered out below before judging document shape, so
    the heuristic looks at actual prose content, not structural noise.
    """
    import re

    raw_blocks = [p.strip() for p in text.split("\n\n") if p.strip()]

    # Filter out markdown headings (#, ##, ###...) and other short
    # structural fragments (captions, footnote markers, table remnants
    # that leaked past the table/text split) before judging document
    # shape - these inflate the "paragraph" count without being real
    # prose content units, and are what caused a long report to be
    # misclassified as FAQ-shaped in testing.
    MIN_PROSE_LEN = 60  # below this, treat as a structural fragment, not a real paragraph
    prose_blocks = [
        p for p in raw_blocks
        if not re.match(r'^#{1,6}\s', p)          # markdown headings
        and not p.startswith("|")                  # stray table lines
        and len(p) >= MIN_PROSE_LEN
    ]

    # Too few natural prose sections to have meaningfully distinct topics -
    # recursive splitting on paragraph/sentence boundaries is sufficient.
    if len(prose_blocks) <= 2:
        return "recursive"

    avg_paragraph_len = sum(len(p) for p in prose_blocks) / len(prose_blocks)

    # On a paid embedding provider, avoid the per-sentence cost on large
    # documents - fall back to the free recursive path past a size threshold.
    if embedding_provider != "local" and len(text) > 20_000:
        return "recursive"

    # Many short, distinct-looking prose paragraphs = FAQ/knowledge-base
    # shaped content, where fixed-size splitting tends to merge unrelated
    # topics. A long report's prose paragraphs are typically much longer
    # than an FAQ answer even after filtering structural noise, so this
    # threshold should now only fire on genuinely FAQ/KB-shaped content.
    if len(prose_blocks) >= 4 and avg_paragraph_len < 400:
        return "semantic"

    return "recursive"


def chunk_text(text: str) -> list[str]:
    """
    Recursive splitter tries to break on paragraph -> sentence -> word
    boundaries in that order, which keeps chunks semantically coherent
    instead of cutting mid-sentence.

    Limitation: chunk_size is still a fixed character ceiling, so several
    short, topically distinct paragraphs under that ceiling get merged into
    one chunk regardless of topic boundaries - this is what happened with
    the civic-complaint FAQ (4 unrelated categories merged into one 727-char
    chunk at chunk_size=800). Fine as a fast, free, deterministic default;
    use semantic_chunk_text() below when documents mix multiple short,
    distinct topics and a fixed size threshold isn't reliable across clients.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_text(text)
    return [c.strip() for c in chunks if len(c.strip()) > 20]


def semantic_chunk_text(text: str, breakpoint_percentile: float = 85.0) -> list[str]:
    """
    Splits on topic boundaries rather than a fixed character count.

    Method (the standard "embedding breakpoint" approach):
      1. Split text into sentences.
      2. Embed each sentence.
      3. Compute cosine distance between each pair of adjacent sentences.
      4. Wherever that distance spikes above the Nth percentile of all
         distances in the document, that's a topic boundary - split there.
      5. Cap any resulting chunk that's still too large by recursively
         splitting it with NO overlap (see _split_oversized below) - using
         overlap here would reintroduce broken-sentence/duplicate fragment
         artifacts across the split boundary, which semantic chunking is
         meant to avoid in the first place.

    HONEST CAVEAT on breakpoint_percentile=85: this is an untuned
    placeholder, not a validated default. Percentile thresholding gets
    more statistically stable as sentence count grows - on a short
    document (a handful of sentences), "Nth percentile" is a brittle
    concept, since there isn't enough data for a percentile to represent a
    real distributional cutoff rather than one outlier gap. On a large,
    naturally-flowing document this matters much less. Don't trust this
    number without validating it against eval/run_eval.py on real,
    representative document sizes for your actual corpus - that's the
    right way to pick this, not guessing a constant from one test case.

    This adapts per-document automatically - a document with many short,
    distinct topics (like an FAQ) naturally produces many small chunks;
    a document with long, continuous prose on one topic produces fewer,
    larger chunks. No manual chunk_size tuning needed per client.

    Cost tradeoff vs chunk_text(): one embedding call per sentence at
    ingestion time, and boundaries are data-dependent rather than
    deterministic - worth it for FAQ/knowledge-base style content, probably
    unnecessary for long uniform prose (a single article, a contract) where
    fixed recursive splitting is already fine and cheaper.
    """
    import re
    import numpy as np
    from app.services.embedding import embed_texts

    # Rejoin PDF-extraction hyphenation artifacts ("con- flict" -> "conflict")
    # before sentence splitting - a stray mid-word period-like break here
    # corrupts sentence boundaries and feeds malformed fragments into the
    # embedding-distance breakpoint detection below.
    text = re.sub(r'(\w)-\s+(\w)', r'\1\2', text)

    # naive sentence split - good enough for this purpose, avoids pulling in
    # a full NLP tokenizer dependency
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    if len(sentences) <= 1:
        return chunk_text(text)

    embeddings = np.array(embed_texts(sentences))

    # cosine distance between each sentence and the next
    distances = []
    for i in range(len(embeddings) - 1):
        a, b = embeddings[i], embeddings[i + 1]
        cos_sim = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
        distances.append(1 - cos_sim)

    if not distances:
        return chunk_text(text)

    threshold = np.percentile(distances, breakpoint_percentile)
    breakpoints = {i for i, d in enumerate(distances) if d > threshold}

    raw_chunks = []
    current = [sentences[0]]
    for i in range(1, len(sentences)):
        if (i - 1) in breakpoints:
            raw_chunks.append(" ".join(current))
            current = [sentences[i]]
        else:
            current.append(sentences[i])
    raw_chunks.append(" ".join(current))

    # cap oversized semantic chunks so token cost stays bounded downstream.
    # Zero overlap here deliberately - this is a safety-net split, not a
    # normal chunking pass, and overlap would reintroduce duplicate/broken
    # sentence fragments across the split boundary.
    final_chunks = []
    for c in raw_chunks:
        if len(c) > settings.chunk_size * 1.5:
            final_chunks.extend(_split_oversized(c, settings.chunk_size))
        elif len(c.strip()) > 20:
            final_chunks.append(c.strip())

    return final_chunks


def _split_oversized(text: str, max_size: int) -> list[str]:
    """
    No-overlap fallback splitter, used only when a semantic chunk exceeds
    the size cap. Sentence-boundary aware (via the same separator priority
    as chunk_text) but with overlap=0, so it never duplicates or fragments
    a sentence across the split - unlike the overlap-based chunk_text used
    for the primary recursive path.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_size,
        chunk_overlap=0,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return [c.strip() for c in splitter.split_text(text) if len(c.strip()) > 20]
