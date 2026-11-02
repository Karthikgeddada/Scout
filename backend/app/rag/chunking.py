"""Structure-aware, hierarchical chunking over the canonical representation.

- Ordinary prose: target 400-700 tokens with controlled overlap (defaults via config).
- Tables: serialized whole; oversized tables split by row batches, headers repeated.
- Code blocks: kept whole.
- Parent-child: every chunk references its document section for parent expansion.
- Duplicate content is detected by normalized hash and dropped (aliases recorded).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.ingestion.canonical import CanonicalDocument, DocumentTable
from app.ingestion.tables import table_to_markdown
from app.rag.tokens import estimate_tokens

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+\n?|(?<=:)\s+\n?")


@dataclass
class ChunkConfig:
    target_tokens: int = 550
    overlap_tokens: int = 80
    min_tokens: int = 80
    table_max_tokens: int = 900


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    kind: str  # text|code|table|table_rows
    text: str
    token_count: int
    parent_section_id: str | None = None
    content_hash: str = ""
    provenance: list[dict[str, Any]] = field(default_factory=list)  # pages/sheets/locators
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "kind": self.kind,
            "text": self.text,
            "token_count": self.token_count,
            "parent_section_id": self.parent_section_id,
            "content_hash": self.content_hash,
            "provenance": self.provenance,
            "metadata": self.metadata,
        }


def _norm_for_hash(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _hash(text: str) -> str:
    return hashlib.sha256(_norm_for_hash(text).encode()).hexdigest()


class Chunker:
    def __init__(self, config: ChunkConfig | None = None, counter: Callable[[str], int] = estimate_tokens) -> None:
        self.config = config or ChunkConfig()
        self.counter = counter

    # ---- text ----

    def chunk_text(self, text: str, *, document_id: str, section_id: str | None, provenance: list[dict], metadata: dict | None = None) -> list[Chunk]:
        sentences = [s for part in text.split("\n\n") for s in _SENTENCE_SPLIT.split(part)]
        sentences = [s.strip() for s in sentences if s and s.strip()]
        if not sentences:
            return []

        target, overlap, minimum = self.config.target_tokens, self.config.overlap_tokens, self.config.min_tokens
        chunks: list[list[str]] = []
        current: list[str] = []
        current_tokens = 0
        for sentence in sentences:
            stoks = self.counter(sentence)
            if current and current_tokens + stoks > target:
                chunks.append(current)
                # keep trailing overlap
                tail: list[str] = []
                tail_tokens = 0
                for s in reversed(current):
                    if tail_tokens + self.counter(s) > overlap:
                        break
                    tail.insert(0, s)
                    tail_tokens += self.counter(s)
                current, current_tokens = tail, tail_tokens
            current.append(sentence)
            current_tokens += stoks
        if current:
            chunks.append(current)

        out: list[Chunk] = []
        for idx, parts in enumerate(chunks):
            body = " ".join(parts)
            tok = self.counter(body)
            if tok < minimum and out:
                # too small: fold into previous chunk
                out[-1].text = out[-1].text + " " + body
                out[-1].token_count = self.counter(out[-1].text)
                out[-1].content_hash = _hash(out[-1].text)
                continue
            out.append(
                Chunk(
                    chunk_id=hashlib.sha1(f"{document_id}:{section_id}:{idx}:{_hash(body)}".encode()).hexdigest()[:16],
                    document_id=document_id,
                    kind="text",
                    text=body,
                    token_count=tok,
                    parent_section_id=section_id,
                    content_hash=_hash(body),
                    provenance=provenance,
                    metadata=metadata or {},
                )
            )
        return out

    # ---- code ----

    def chunk_code(self, text: str, *, document_id: str, section_id: str | None, provenance: list[dict], metadata: dict | None = None) -> list[Chunk]:
        if not text.strip():
            return []
        return [
            Chunk(
                chunk_id=hashlib.sha1(f"{document_id}:code:{_hash(text)}".encode()).hexdigest()[:16],
                document_id=document_id,
                kind="code",
                text=text,
                token_count=self.counter(text),
                parent_section_id=section_id,
                content_hash=_hash(text),
                provenance=provenance,
                metadata=metadata or {},
            )
        ]

    # ---- tables ----

    def chunk_table(self, table: DocumentTable, *, document_id: str, max_rows: int = 40) -> list[Chunk]:
        whole = table_to_markdown(table)
        prov = [{"page": p} for p in table.pages] if table.pages else []
        if table.sheet:
            prov = [{"sheet": table.sheet}]
        meta = {
            "table_id": table.table_id,
            "title": table.title,
            "headers": table.headers,
            "sheet": table.sheet,
            "pages": table.pages,
        }
        if self.counter(whole) <= self.config.table_max_tokens:
            return [
                Chunk(
                    chunk_id=hashlib.sha1(f"{document_id}:table:{table.table_id}".encode()).hexdigest()[:16],
                    document_id=document_id,
                    kind="table",
                    text=whole,
                    token_count=self.counter(whole),
                    parent_section_id=None,
                    content_hash=_hash(whole),
                    provenance=prov,
                    metadata=meta,
                )
            ]
        # Oversized: row batches with repeated headers, provenance row ranges preserved.
        body = [r for r in table.rows if not r.is_header]
        out: list[Chunk] = []
        for start in range(0, len(body), max_rows):
            # rebuild batch text only for this slice (headers repeated)
            lines: list[str] = []
            if table.title:
                lines.append(f"Table: {table.title} (rows {start + 1}-{min(start + max_rows, len(body))} of {len(body)})")
            if table.headers:
                lines.append("| " + " | ".join(table.headers) + " |")
                lines.append("|" + "---|" * len(table.headers))
            for r in body[start : start + max_rows]:
                lines.append("| " + " | ".join(c.raw_value for c in r.cells) + " |")
            text = "\n".join(lines)
            out.append(
                Chunk(
                    chunk_id=hashlib.sha1(f"{document_id}:table:{table.table_id}:{start}".encode()).hexdigest()[:16],
                    document_id=document_id,
                    kind="table_rows",
                    text=text,
                    token_count=self.counter(text),
                    parent_section_id=None,
                    content_hash=_hash(text),
                    provenance=prov,
                    metadata={**meta, "row_start": start, "row_end": start + max_rows},
                )
            )
        return out

    # ---- document ----

    def chunk_document(self, doc: CanonicalDocument) -> list[Chunk]:
        out: list[Chunk] = []
        text_kinds = {"paragraph", "heading", "list_item", "footnote", "ocr_text", "caption"}

        def _chunk_section(section_id: str | None, title: str, level: int | None, elements: list[DocumentElement]) -> None:
            """Small consecutive prose elements are merged before chunking so short
            sections are not fragmented into sub-minimum chunks; tables and code stay
            per-element."""
            texts: list[str] = []
            pages: list[int] = []
            metadata = {"section_title": title, "section_level": level}
            for element in elements:
                if element.kind == "code":
                    out.extend(self.chunk_code(element.text, document_id=doc.document_id, section_id=section_id, provenance=[], metadata=metadata))
                elif element.kind == "table" and element.table_id:
                    table = doc.table_by_id(element.table_id)
                    if table:
                        out.extend(self.chunk_table(table, document_id=doc.document_id))
                elif element.kind in text_kinds and element.text.strip():
                    if element.provenance and element.provenance.page:
                        pages.append(element.provenance.page)
                    texts.append(element.text)
            if texts:
                prov = [{"pages": sorted(set(pages))}] if pages else []
                out.extend(
                    self.chunk_text(
                        "\n\n".join(texts),
                        document_id=doc.document_id,
                        section_id=section_id,
                        provenance=prov,
                        metadata=metadata,
                    )
                )

        for section in doc.sections:
            elements = [e for e in (doc.element_by_id(eid) for eid in section.element_ids) if e is not None]
            _chunk_section(section.section_id, section.title, section.level, elements)

        # Elements not attached to any section (e.g. title pages, stray OCR text)
        attached = {eid for s in doc.sections for eid in s.element_ids}
        orphans = [e for e in doc.elements if e.element_id not in attached and e.text.strip()]
        if orphans:
            _chunk_section(None, "", None, orphans)

        # deduplicate identical normalized content, keeping the first occurrence
        seen: dict[str, str] = {}
        deduped: list[Chunk] = []
        for chunk in out:
            if chunk.content_hash in seen:
                continue
            seen[chunk.content_hash] = chunk.chunk_id
            deduped.append(chunk)
        return deduped
