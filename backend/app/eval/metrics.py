"""RAG evaluation metrics: retrieval quality, context quality, citation validity,
numeric correctness. Pure stdlib so evaluation runs anywhere."""

from __future__ import annotations

import math
from decimal import Decimal


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = set(retrieved[:k])
    return len(top & relevant) / len(relevant)


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    top = retrieved[:k]
    return sum(1 for c in top if c in relevant) / k


def mrr(retrieved: list[str], relevant: set[str]) -> float:
    for rank, cand in enumerate(retrieved, start=1):
        if cand in relevant:
            return 1.0 / rank
    return 0.0


def ndcg(retrieved: list[str], relevant: set[str], k: int) -> float:
    dcg = 0.0
    for i, cand in enumerate(retrieved[:k], start=1):
        if cand in relevant:
            dcg += 1.0 / math.log2(i + 1)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def context_precision(context_chunks: list[str], relevant: set[str]) -> float:
    """Share of context items that are relevant (binary relevance)."""
    if not context_chunks:
        return 0.0
    return sum(1 for c in context_chunks if c in relevant) / len(context_chunks)


def context_recall(context_chunks: list[str], relevant: set[str]) -> float:
    """Share of relevant items present in the context."""
    if not relevant:
        return 0.0
    return len(set(context_chunks) & relevant) / len(relevant)


def citation_validity_rate(claimed: list[str], verified: list[str]) -> float:
    """Fraction of claimed citations that passed server-side verification."""
    if not claimed:
        return 0.0
    verified_set = set(verified)
    return sum(1 for c in claimed if c in verified_set) / len(claimed)


def numeric_correctness(computed: list, expected: list[str | Decimal], tolerance: str = "0.01") -> float:
    """Decimal-safe comparison of computed vs expected values."""
    if not expected:
        return 0.0
    tol = Decimal(tolerance)
    hit = 0
    for got, want in zip(computed, expected):
        try:
            if abs(Decimal(str(got)) - Decimal(str(want))) <= tol:
                hit += 1
        except Exception:
            pass
    return hit / len(expected)
