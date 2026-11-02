"""Parser tests: markdown/txt/csv paths run everywhere (stdlib only).
Binary-format parsers (pdf/docx/xlsx) are covered by integration tests that need
their libraries installed."""

from app.ingestion.canonical import CanonicalDocument
from app.parsers.spreadsheets import parse_csv
from app.parsers.structured_text import parse_markdown, parse_txt


def test_markdown_sections_and_code():
    md = (
        "# Title\n\nintro paragraph\n\n## Sub\n\n- item one\n- item two\n\n"
        "```python\nprint('hi')\n```\n\nplain tail\n"
    ).encode()
    doc = parse_markdown(md, document_id="d1", filename="x.md")
    kinds = [e.kind for e in doc.elements]
    assert "heading" in kinds and "code" in kinds and "paragraph" in kinds
    assert [s.title for s in doc.sections] == ["Title", "Sub"]
    code = next(e for e in doc.elements if e.kind == "code")
    assert "print('hi')" in code.text
    assert doc.fmt == "md"


def test_markdown_list_items():
    md = "# H\n\n- a\n- b\n".encode()
    doc = parse_markdown(md, document_id="d2", filename="y.md")
    assert any(e.kind == "list_item" for e in doc.elements)


def test_txt_paragraphs():
    txt = b"first para\n\nsecond para\n\n\nthird para"
    doc = parse_txt(txt, document_id="d3", filename="z.txt")
    assert len([e for e in doc.elements if e.kind == "paragraph"]) == 3


def test_csv_delimiter_and_numeric_cells():
    csv_data = "region;revenue;year\nEU;1,200.50;2023\nUS;(45,000);2024\n".encode("utf-8")
    doc = parse_csv(csv_data, document_id="d4", filename="fin.csv")
    assert doc.metadata["delimiter"] == ";"
    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert table.headers == ["region", "revenue", "year"]
    body = [r for r in table.rows if not r.is_header]
    eu_rev = body[0].cells[1]
    us_rev = body[1].cells[1]
    assert eu_rev.data_type in {"currency", "number"}
    assert eu_rev.decimal_value == "1200.50"
    assert us_rev.is_negative is True


def test_csv_empty_rejected_as_empty_table():
    doc = parse_csv(b"", document_id="d5", filename="empty.csv")
    assert doc.tables == []


def test_canonical_roundtrip_markdown():
    doc = parse_markdown(b"# A\n\ntext\n", document_id="d6", filename="a.md")
    restored = CanonicalDocument.from_dict(doc.to_dict())
    assert len(restored.elements) == len(doc.elements)
    assert restored.sections[0].title == doc.sections[0].title
