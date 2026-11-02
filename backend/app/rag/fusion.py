"""Hybrid retrieval fusion: Reciprocal Rank Fusion over dense + lexical rankings."""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    *,
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Fuse candidate rankings (each an ordered list of candidate ids).

    score(c) = sum_i w_i / (k + rank_i(c)). Duplicates within a ranking collapse to
    their best rank. Weights default to 1.0 per ranking.
    """
    if not rankings:
        return []
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights length must match rankings length")

    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        seen: set[str] = set()
        for pos, candidate in enumerate(ranking):
            if candidate in seen:  # duplicate ids inside one ranking: keep best rank
                continue
            seen.add(candidate)
            scores[candidate] = scores.get(candidate, 0.0) + weight / (k + pos + 1)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def dedupe_preserving_order(ids: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def top_n(ranking: list[tuple[str, float]], n: int) -> list[tuple[str, float]]:
    return ranking[: max(0, n)]
