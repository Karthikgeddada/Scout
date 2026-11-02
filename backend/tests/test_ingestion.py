"""Tests for file detection, canonical representation and the table subsystem."""

from __future__ import annotations

from decimal import Decimal

from app.ingestion.canonical import (
    CanonicalDocument,
    DocumentElement,
    DocumentSection,
    DocumentTable,
    Provenance,
)
from app.ingestion.detection import detect_format, validate_upload
from app.ingestion.tables import build_cell, normalize_headers, parse_numeric, validate_table


def test_pdf_signature_beats_extension():
    detected = detect_format("report.docx", b"%PDF-1.7 fake body")
    assert detected.fmt == "pdf"
    assert detected.method == "signature"
    assert detected.confidence == 1.0


def test_png_and_jpeg_signatures():
    assert detect_format("x.bin", b"\x89PNG\r\n\x1a\nrest").fmt == "png"
    assert detect_format("y.bin", b"\xff\xd8\xff\xe0...").fmt == "jpeg"


def test_ooxml_members_decide_flavour():
    # A zip header alone must not be trusted as docx; members decide.
    members = {"xl/workbook.xml", "xl/worksheets/sheet1.xml", "[Content_Types].xml"}
    detected = detect_format("anything.pdf", b"PK\x03\x04", ooxml_members=members)
    assert detected.fmt == "xlsx"
    assert detected.method == "ooxml-member"


def test_csv_heuristic():
    sample = b"name,amount\nAlpha,10\nBeta,20\nGamma,30\n"
    detected = detect_format("data.txt", sample[:8], sample=sample)
    assert detected.fmt == "csv"


def test_markdown_heuristic():
    sample = b"# Title\n\n- item one\n- item two\n"
    detected = detect_format("notes.txt", sample[:8], sample=sample)
    assert detected.fmt == "md"


def test_plain_text():
    sample = b"Just some ordinary prose without any structure markers at all."
    detected = detect_format("readme", sample[:8], sample=sample)
    assert detected.fmt == "txt"


def test_validate_upload_rejects():
    detected = detect_format("evil.exe", b"MZ\x90\x00")
    assert validate_upload(detected, 10, 1000)  # unsupported format
    detected_csv = detect_format("ok.csv", b"a,b\n1,2\n")
    assert not validate_upload(detected_csv, 10, 1000)
    assert validate_upload(detected_csv, 2000, 1000)  # too big


def test_parse_numeric_forms():
    cases = {
        "1,234.56": Decimal("1234.56"),
        "(1,234.56)": Decimal("-1234.56"),
        "$1.2M": Decimal("1200000"),
        "12.3%": Decimal("12.3"),
        "USD 500": Decimal("500"),
        "-42": Decimal("-42"),
        "1.234,56": Decimal("1234.56"),
        "N/A": None,
        "": None,
        "hello": None,
    }
    for raw, expected in cases.items():
        parsed = parse_numeric(raw)
        if expected is None:
            assert parsed is None, raw
        else:
            assert parsed is not None, raw
            assert Decimal(parsed["decimal_value"]) == expected, raw


def test_parse_numeric_currency_and_sign():
    parsed = parse_numeric("($1,500.00)")
    assert parsed["currency"] == "USD"
    assert parsed["is_negative"] is True
    assert Decimal(parsed["decimal_value"]) == Decimal("-1500.00")


def test_normalize_headers_dedupes():
    assert normalize_headers(["Revenue", "", "Revenue", "revenue"]) == [
        "Revenue", "column", "Revenue (2)", "revenue"
    ]


def test_validate_table_flags_totals_mismatch():
    from app.ingestion.canonical import TableRow

    table = DocumentTable(table_id="t1", headers=["item", "amount"])
    table.rows = [
        TableRow(cells=[build_cell("Alpha", 0, 0), build_cell("100.00", 0, 1)], is_header=False),
        TableRow(cells=[build_cell("Beta", 1, 0), build_cell("200.00", 1, 1)], is_header=False),
        TableRow(cells=[build_cell("Total", 2, 0), build_cell("999.00", 2, 1)], is_header=False),
    ]
    flags = validate_table(table)
    assert any(f.startswith("totals_mismatch") for f in flags)
    assert "totals_row:2" in flags


def test_validate_table_consistent_sums_pass():
    from app.ingestion.canonical import TableRow

    table = DocumentTable(
        table_id="t2",
        headers=["item", "amount"],
        rows=[
            TableRow(cells=[build_cell("A", 0, 0), build_cell("100.00", 0, 1)]),
            TableRow(cells=[build_cell("B", 1, 0), build_cell("200.00", 1, 1)]),
            TableRow(cells=[build_cell("Total", 2, 0), build_cell("300.00", 2, 1)]),
        ],
    )
    flags = validate_table(table)
    assert not any(f.startswith("totals_mismatch") for f in flags)


def test_canonical_json_roundtrip():
    from app.ingestion.canonical import TableRow

    section = DocumentSection(section_id="s1", title="Overview", level=1)
    element = DocumentElement(
        element_id="e1",
        kind="paragraph",
        text="Revenue grew 20% year over year.",
        section_id="s1",
        provenance=Provenance(page=3, locator="A1:B2"),
    )
    table = DocumentTable(
        table_id="tab1",
        title="Income",
        headers=["year", "revenue"],
        rows=[
            TableRow(cells=[build_cell("2023", 0, 0), build_cell("$1,000", 0, 1)])
        ],
        pages=[5],
    )
    doc = CanonicalDocument(
        document_id="d1",
        source_filename="fin.pdf",
        fmt="pdf",
        mime="application/pdf",
        sections=[section],
        elements=[element],
        tables=[table],
    )
    doc2 = CanonicalDocument.from_dict(doc.to_dict())
    assert doc2.elements[0].provenance.page == 3
    assert doc2.tables[0].rows[0].cells[1].decimal_value == "1000"
    assert doc2.tables[0].rows[0].cells[1].currency == "USD"
    assert doc2.elements[0].text == element.text
