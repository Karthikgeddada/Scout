"""Server-side citation verification.

Every citation the model emits is checked against the chunk registry built from
retrieved evidence. LLM-claimed pages/locations are never trusted blindly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class RetrievedEvidence:
    """What the server actually knows about a retrieved chunk."""

    evidence_id: int          # [E1], [E2] label used in the prompt
    chunk_id: str
    document_id: str
    document_title: str
    text: str
    pages: list[int]
    sheet: str | None = None
    locator: str | None = None  # cell range or heading anchor
    table_id: str | None = None


@dataclass
class ClaimedCitation:
    """What the model claims (parsed from its output)."""

    evidence_id: int
    page: int | None = None
    sheet: str | None = None
    locator: str | None = None
    quote: str | None = None


@dataclass
class VerifiedCitation:
    evidence_id: int
    chunk_id: str
    document_id: str
    document_title: str
    page: int | None
    sheet: str | None
    locator: str | None
    quote: str | None


@dataclass
class CitationCheck:
    verified: VerifiedCitation | None
    reason: str | None = None


_CITATION_RE = re.compile(r"\[E(\d+)\]")


def parse_citation_markers(text: str) -> list[int]:
    """Return the evidence ids a generated answer references, in order."""
    seen: list[int] = []
    for m in _CITATION_RE.finditer(text or ""):
        eid = int(m.group(1))
        if eid not in seen:
            seen.append(eid)
    return seen


class CitationValidator:
    def __init__(self, registry: dict[int, RetrievedEvidence], *, quote_similarity: float = 0.75) -> None:
        self._registry = registry
        self._quote_similarity = quote_similarity

    def validate(self, claimed: ClaimedCitation) -> CitationCheck:
        evidence = self._registry.get(claimed.evidence_id)
        if evidence is None:
            return CitationCheck(None, f"unknown evidence id {claimed.evidence_id}")

        # Page must be one the evidence actually covers; reject invented pages.
        if claimed.page is not None and evidence.pages and claimed.page not in evidence.pages:
            return CitationCheck(
                None,
                f"page {claimed.page} not in evidence pages {evidence.pages}",
            )

        # Sheet/locator must match the evidence's stored provenance.
        if claimed.sheet and evidence.sheet and claimed.sheet != evidence.sheet:
            return CitationCheck(None, f"sheet {claimed.sheet!r} != evidence sheet {evidence.sheet!r}")

        if claimed.quote:
            if not self._quote_in_evidence(claimed.quote, evidence.text):
                return CitationCheck(None, "quote not present in cited evidence")

        return CitationCheck(
            VerifiedCitation(
                evidence_id=evidence.evidence_id,
                chunk_id=evidence.chunk_id,
                document_id=evidence.document_id,
                document_title=evidence.document_title,
                page=claimed.page if claimed.page is not None else (evidence.pages[0] if evidence.pages else None),
                sheet=evidence.sheet,
                locator=evidence.locator,
                quote=claimed.quote,
            )
        )

    def _quote_in_evidence(self, quote: str, evidence_text: str) -> bool:
        norm = lambda s: re.sub(r"\s+", " ", s.strip().lower())  # noqa: E731
        if norm(quote) in norm(evidence_text):
            return True
        ratio = SequenceMatcher(None, norm(quote), norm(evidence_text)).find_longest_match(
            0, len(norm(quote)), 0, len(norm(evidence_text))
        )
        return (ratio.size / max(1, len(norm(quote)))) >= self._quote_similarity
