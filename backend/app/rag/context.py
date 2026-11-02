"""Context construction: parent expansion, neighbours, table context, dedup,
token-budget packing and prioritized evidence blocks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.rag.tokens import estimate_tokens


@dataclass
class ScoredChunk:
    chunk_id: str
    document_id: str
    document_title: str
    text: str
    score: float
    kind: str = "text"
    pages: list[int] = field(default_factory=list)
    sheet: str | None = None
    locator: str | None = None
    section_title: str | None = None
    parent_text: str | None = None   # parent section text (hierarchical expansion)
    parent_section_id: str | None = None
    neighbours: list[str] = field(default_factory=list)  # adjacent chunk texts
    order_key: tuple[str, int] = ("", 0)  # document_id + sequence for ordering


@dataclass
class EvidenceBlock:
    evidence_id: int
    chunk_id: str
    document_id: str
    document_title: str
    text: str
    score: float
    pages: list[int]
    sheet: str | None
    locator: str | None


class ContextBuilder:
    """Packs scored chunks into numbered evidence blocks within a token budget.

    Strategy: rank order is priority. For each chunk include itself, then its
    parent-section text (deduplicated), then table headers. Budget-aware packing
    never exceeds max_tokens; overflow is dropped, not truncated mid-sentence.
    """

    def __init__(self, token_budget: int = 12000, counter=estimate_tokens) -> None:
        self.token_budget = token_budget
        self.counter = counter

    def build(self, chunks: list[ScoredChunk]) -> list[EvidenceBlock]:
        selected: list[EvidenceBlock] = []
        used = 0
        seen_hash: set[str] = set()
        next_id = 1
        for chunk in chunks:
            parts: list[str] = []
            if chunk.parent_text and chunk.parent_text.strip():
                parent_hash = hashlib.sha256(chunk.parent_text.strip().lower().encode()).hexdigest()
                if parent_hash not in seen_hash:
                    seen_hash.add(parent_hash)
                    parts.append(f"[section: {chunk.section_title}]\n{chunk.parent_text.strip()}")
            body = chunk.text.strip()
            body_hash = hashlib.sha256(body.lower().encode()).hexdigest()
            if body_hash in seen_hash:
                continue
            seen_hash.add(body_hash)
            parts.append(body)
            for nb in chunk.neighbours:
                nb_hash = hashlib.sha256(nb.strip().lower().encode()).hexdigest()
                if nb_hash not in seen_hash and nb.strip():
                    candidate_tokens = self.counter(nb)
                    if used + candidate_tokens <= self.token_budget:
                        seen_hash.add(nb_hash)
                        parts.append(nb)
            text = "\n\n".join(parts)
            cost = self.counter(text)
            if used + cost > self.token_budget:
                # try chunk body alone; if still over, drop (never truncate mid-sentence)
                cost_body = self.counter(body)
                if used + cost_body > self.token_budget:
                    continue
                text = body
                cost = cost_body
            used += cost
            selected.append(
                EvidenceBlock(
                    evidence_id=next_id,
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_title=chunk.document_title,
                    text=text,
                    score=chunk.score,
                    pages=chunk.pages,
                    sheet=chunk.sheet,
                    locator=chunk.locator,
                )
            )
            next_id += 1
        return selected


def render_context(blocks: list[EvidenceBlock]) -> str:
    """Render evidence blocks with [E#] labels the model must cite."""
    rendered: list[str] = []
    for block in blocks:
        src = []
        if block.document_title:
            src.append(block.document_title)
        if block.pages:
            src.append(f"page {', '.join(str(p) for p in block.pages)}")
        if block.sheet:
            src.append(f"sheet '{block.sheet}'")
        if block.locator:
            src.append(f"cells {block.locator}")
        header = f"[E{block.evidence_id}] ({'; '.join(src)})"
        rendered.append(f"{header}\n{block.text}")
    return "\n\n".join(rendered)
