"""Tests for structure-aware chunking."""

from __future__ import annotations

from app.ingestion.canonical import (
    CanonicalDocument,
    DocumentElement,
    DocumentSection,
    DocumentTable,
    Provenance,
    TableRow,
)
from app.ingestion.tables import build_cell, table_to_markdown
from app.rag.chunking import ChunkConfig, Chunker


def _doc_with_prose(paragraphs: list[str]) -> CanonicalDocument:
    doc = CanonicalDocument(
        document_id="doc1", source_filename="x.pdf", fmt="pdf", mime="application/pdf"
    )
    section = DocumentSection(section_id="s1", title="Results", level=1)
    doc.sections.append(section)
    for i, p in enumerate(paragraphs):
        eid = f"e{i}"
        doc.elements.append(
            DocumentElement(
                element_id=eid, kind="paragraph", text=p, section_id="s1",
                provenance=Provenance(page=1 + i),
            )
        )
        section.element_ids.append(eid)
    return doc


def test_long_prose_is_split_with_overlap_and_bounds():
    para = (
        "Revenue increased strongly in the reported period. "
        "Operating costs were contained through disciplined spend management. "
        "The EBITDA margin expanded materially. "
    ) * 20
    doc = _doc_with_prose([para])
    chunker = Chunker(ChunkConfig(target_tokens=100, overlap_tokens=20, min_tokens=10))
    chunks = chunker.chunk_document(doc)
    assert len(chunks) >= 2
    assert all(c.token_count <= 130 for c in chunks)  # target + small slack
    assert all(c.kind == "text" for c in chunks)
    assert all(c.parent_section_id == "s1" for c in chunks)
    # controlled overlap: consecutive chunks share some text
    if len(chunks) >= 2:
        assert chunks[0].text[-30:].strip() and chunks[1].text[:30]


def test_small_sections_are_not_fragmented():
    doc = _doc_with_prose(["Short fact one.", "Short fact two.", "Short fact three."])
    chunker = Chunker(ChunkConfig(target_tokens=550, overlap_tokens=80, min_tokens=80))
    chunks = chunker.chunk_document(doc)
    assert len(chunks) == 1
    assert "Short fact one." in chunks[0].text
    assert "Short fact three." in chunks[0].text


def test_duplicate_content_is_deduplicated():
    doc = _doc_with_prose(["Identical sentence about clause 7.2." for _ in range(4)])
    chunker = Chunker(ChunkConfig(target_tokens=550, overlap_tokens=80, min_tokens=10))
    chunks = chunker.chunk_document(doc)
    assert len(chunks) == 1


def test_code_blocks_kept_whole():
    doc = _doc_with_prose([])
    doc.sections[0].element_ids.append("code1")
    doc.elements.append(
        DocumentElement(
            element_id="code1", kind="code", section_id="s1",
            text="def total(xs):\n    return sum(xs)\n",
        )
    )
    chunker = Chunker(ChunkConfig(target_tokens=10, overlap_tokens=2, min_tokens=2))
    chunks = chunker.chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0].kind == "code"
    assert "def total" in chunks[0].text


def test_oversized_table_splits_by_rows_with_headers():
    table = DocumentTable(table_id="big", title="Ledger", headers=["id", "amount"])
    for i in range(120):
        table.rows.append(
            TableRow(cells=[build_cell(f"R{i}", i, 0), build_cell(f"{i * 10}", i, 1)])
        )
    chunker = Chunker(ChunkConfig(target_tokens=550, overlap_tokens=80, min_tokens=80, table_max_tokens=200))
    chunks = chunker.chunk_table(table, document_id="doc1", max_rows=40)
    assert len(chunks) > 1
    assert all(c.kind == "table_rows" for c in chunks)
    # headers repeated in every batch
    for c in chunks:
        assert "| id | amount |" in c.text
    # row ranges do not overlap and cover all rows
    starts = [c.metadata["row_start"] for c in chunks]
    assert starts == sorted(starts) and starts[0] == 0 and starts[-1] == 80


def test_table_markdown_serialization():
    table = DocumentTable(table_id="t", title="Revenue", headers=["year", "revenue"], pages=[7])
    table.rows.append(TableRow(cells=[build_cell("2023", 0, 0), build_cell("$1,000", 0, 1)]))
    md = table_to_markdown(table)
    assert "Table: Revenue" in md
    assert "page 7" in md
    assert "| 2023 | $1,000 |" in md
