"""
Lightweight RAG eval harness. Not RAGAS-scale, but enough to answer the
question that actually matters before tuning retrieval further: "did this
change help or hurt, measured against a fixed set of queries?"

Usage:
    python -m eval.run_eval --tenant-id <tenant_id>                  # full eval incl. generation (costs LLM calls)
    python -m eval.run_eval --tenant-id <tenant_id> --no-generation  # retrieval/rerank only, free

Metrics reported:
  - Retrieval Hit Rate@k   : did the pre-rerank candidate set contain a
                              chunk matching the expected keywords, for
                              "answerable" queries
  - Rerank Hit Rate@k      : same, but after reranking down to the smaller
                              top_k actually sent to the LLM - this is the
                              number that matters, since it's what the LLM
                              actually sees
  - MRR (rerank)           : mean reciprocal rank of the first matching
                              chunk post-rerank - rewards relevant chunks
                              landing near the top, not just "somewhere in k"
  - Citation Rate          : did the generated answer include at least one
                              [n] citation marker (answerable queries only)
  - Groundedness (heuristic): does the generated answer contain the expected
                              keywords - a crude proxy, not a substitute for
                              human review, but catches obvious drift
  - Refusal Accuracy       : for "unanswerable" queries, did the system
                              correctly decline rather than fabricate an answer
"""
import sys
import json
import argparse
import re

sys.path.insert(0, ".")

from app.services.retrieval import retrieve
from app.services.reranking import rerank
from app.services.generation import stream_answer, build_citations
from app.config import get_settings

settings = get_settings()

REFUSAL_PATTERNS = [
    "don't have", "do not have", "isn't in the context", "not in the context",
    "no information", "cannot find", "can't find", "doesn't contain",
    "does not contain", "unable to find", "not mentioned", "not covered",
]


def contains_any(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def first_match_rank(chunks: list[dict], keywords: list[str]) -> int | None:
    """1-indexed rank of the first chunk containing any expected keyword, or None."""
    for i, c in enumerate(chunks, start=1):
        if contains_any(c["text"], keywords):
            return i
    return None


def is_refusal(answer: str) -> bool:
    answer_lower = answer.lower()
    return any(p in answer_lower for p in REFUSAL_PATTERNS)


def has_citation_marker(answer: str) -> bool:
    return bool(re.search(r"\[\d+\]", answer))


def run_generation(query: str, chunks: list[dict]) -> str:
    return "".join(stream_answer(query, chunks))


def evaluate(tenant_id: str, dataset_path: str, run_gen: bool, top_k_retrieve: int, top_k_rerank: int):
    with open(dataset_path) as f:
        dataset = json.load(f)

    results = []

    for item in dataset:
        qid, qtype, query = item["id"], item["type"], item["query"]
        expected_keywords = item.get("expected_keywords", [])

        candidates = retrieve(tenant_id, query, top_k=top_k_retrieve)
        reranked = rerank(query, candidates, top_k=top_k_rerank)

        row = {"id": qid, "type": qtype, "query": query}

        if qtype == "answerable":
            retrieval_hit = contains_any(
                " ".join(c["text"] for c in candidates), expected_keywords
            ) if expected_keywords else None
            rerank_rank = first_match_rank(reranked, expected_keywords) if expected_keywords else None

            row["retrieval_hit"] = retrieval_hit
            row["rerank_hit"] = rerank_rank is not None
            row["rerank_rank"] = rerank_rank
            row["reciprocal_rank"] = (1 / rerank_rank) if rerank_rank else 0.0

            if run_gen:
                answer = run_generation(query, reranked) if reranked else ""
                row["answer"] = answer
                row["citation_present"] = has_citation_marker(answer)
                row["grounded_heuristic"] = contains_any(answer, expected_keywords) if expected_keywords else None

        elif qtype == "unanswerable":
            if run_gen:
                answer = run_generation(query, reranked) if reranked else "I don't have any information about that."
                row["answer"] = answer
                row["correctly_refused"] = is_refusal(answer)

        results.append(row)

    return results


def print_report(results: list[dict], run_gen: bool):
    answerable = [r for r in results if r["type"] == "answerable"]
    unanswerable = [r for r in results if r["type"] == "unanswerable"]

    print("\n" + "=" * 60)
    print("RETRIEVAL REPORT")
    print("=" * 60)
    for r in answerable:
        mark = "✓" if r["rerank_hit"] else "✗"
        print(f"  [{mark}] {r['id']}: \"{r['query'][:55]}...\"  (rerank rank: {r['rerank_rank']})")

    if answerable:
        retrieval_hits = [r for r in answerable if r["retrieval_hit"]]
        rerank_hits = [r for r in answerable if r["rerank_hit"]]
        mrr = sum(r["reciprocal_rank"] for r in answerable) / len(answerable)

        print(f"\n  Retrieval Hit Rate@{len(retrieval_hits)}: {len(retrieval_hits)}/{len(answerable)} "
              f"({100*len(retrieval_hits)/len(answerable):.0f}%)")
        print(f"  Rerank Hit Rate:    {len(rerank_hits)}/{len(answerable)} "
              f"({100*len(rerank_hits)/len(answerable):.0f}%)")
        print(f"  MRR (post-rerank):  {mrr:.3f}")

    if run_gen and answerable:
        print("\n" + "=" * 60)
        print("GENERATION REPORT (answerable queries)")
        print("=" * 60)
        citation_hits = [r for r in answerable if r.get("citation_present")]
        grounded_hits = [r for r in answerable if r.get("grounded_heuristic")]
        print(f"  Citation Rate:      {len(citation_hits)}/{len(answerable)} "
              f"({100*len(citation_hits)/len(answerable):.0f}%)")
        print(f"  Groundedness (kw):  {len(grounded_hits)}/{len(answerable)} "
              f"({100*len(grounded_hits)/len(answerable):.0f}%)  <- heuristic, spot-check these manually")

    if run_gen and unanswerable:
        print("\n" + "=" * 60)
        print("REFUSAL REPORT (unanswerable queries)")
        print("=" * 60)
        for r in unanswerable:
            mark = "✓" if r["correctly_refused"] else "✗"
            print(f"  [{mark}] {r['id']}: \"{r['query'][:55]}...\"")
            if not r["correctly_refused"]:
                print(f"        answer was: {r['answer'][:120]}...")
        refusal_hits = [r for r in unanswerable if r["correctly_refused"]]
        print(f"\n  Refusal Accuracy:   {len(refusal_hits)}/{len(unanswerable)} "
              f"({100*len(refusal_hits)/len(unanswerable):.0f}%)")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True, help="tenant_id to run eval against (must have ingested data)")
    parser.add_argument("--dataset", default="eval/dataset.json")
    parser.add_argument("--output", default="eval/results.json")
    parser.add_argument("--no-generation", action="store_true", help="skip LLM calls, retrieval/rerank metrics only (free)")
    parser.add_argument("--top-k-retrieve", type=int, default=settings.retrieval_top_k)
    parser.add_argument("--top-k-rerank", type=int, default=settings.rerank_top_k)
    args = parser.parse_args()

    results = evaluate(
        tenant_id=args.tenant_id,
        dataset_path=args.dataset,
        run_gen=not args.no_generation,
        top_k_retrieve=args.top_k_retrieve,
        top_k_rerank=args.top_k_rerank,
    )

    print_report(results, run_gen=not args.no_generation)

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Full results written to {args.output}")
