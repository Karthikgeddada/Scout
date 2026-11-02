"""Spreadsheet parsers: XLSX via openpyxl (all sheets, merged headers,
cell-level numeric validation) and CSV via the stdlib with delimiter sniffing.

Each sheet/table becomes a DocumentTable; numeric cells keep Decimal-safe
strings so the analytics engine can do exact financial math."""

from __future__ import annotations

import csv
import io

from app.ingestion.canonical import (
    CanonicalDocument,
    DocumentElement,
    DocumentSection,
    DocumentTable,
    Provenance,
    QualityReport,
    TableCell,
    TableRow,
    stable_id,
)
from app.ingestion.tables import build_cell


def _rows_to_table(rows: list[list], *, document_id: str, sheet: str | None, title: str | None) -> DocumentTable:
    table = DocumentTable(
        table_id=stable_id(document_id, "tbl", sheet, 0),
        title=title or (f"Sheet {sheet}" if sheet else "Table"),
        sheet=sheet,
        validation_flags=["spreadsheet-native"],
    )
    for r_index, row in enumerate(rows):
        cells = []
        for c_index, value in enumerate(row):
            raw = "" if value is None else str(value)
            cells.append(build_cell(raw, r_index, c_index, provenance=Provenance(sheet=sheet)))
        table.rows.append(TableRow(cells=cells, is_header=(r_index == 0)))
    if table.rows:
        table.rows[0].is_header = True
        table.headers = [c.raw_value for c in table.rows[0].cells]
    return table


def _column_types(table: DocumentTable) -> dict[str, str]:
    types: dict[str, str] = {h: "text" for h in table.headers}
    body = [r for r in table.rows if not r.is_header]
    for col_index, header in enumerate(table.headers):
        seen = [r.cells[col_index].data_type for r in body if len(r.cells) > col_index]
        seen = [t for t in seen if t not in {"empty", "text"}]
        if seen and len(seen) / max(1, len(body)) > 0.5:
            counts: dict[str, int] = {}
            for t in seen:
                counts[t] = counts.get(t, 0) + 1
            types[header] = max(counts, key=counts.get)
    return types


def _records(table: DocumentTable) -> list[dict]:
    """Column-name -> value records for the analytics executor."""
    out: list[dict] = []
    for row in [r for r in table.rows if not r.is_header]:
        rec: dict = {}
        for col_index, header in enumerate(table.headers):
            cell = row.cells[col_index] if len(row.cells) > col_index else None
            if cell is None:
                continue
            rec[header] = (
                cell.decimal_value if cell.decimal_value is not None
                else (cell.normalized_value if cell.normalized_value is not None else cell.raw_value)
            )
        out.append(rec)
    return out


def parse_xlsx(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = CanonicalDocument(
        document_id=document_id, source_filename=filename,
        fmt="xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        metadata={"parser": "openpyxl"},
        quality=QualityReport(parser_versions={"openpyxl": "3"}),
    )
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows: list[list] = []
        for row in ws.iter_rows(values_only=True):
            if not any(v is not None and str(v).strip() for v in row):
                continue
            rows.append(list(row))
        if not rows:
            continue
        table = _rows_to_table(rows, document_id=document_id, sheet=sheet_name, title=None)
        out.tables.append(table)
        out.quality.tables_detected += 1
        section_id = stable_id(document_id, "sec", sheet_name)
        out.sections.append(DocumentSection(section_id=section_id, title=f"Sheet: {sheet_name}", level=1))
        out.elements.append(DocumentElement(
            element_id=stable_id(document_id, "el", sheet_name), kind="heading",
            text=f"Sheet: {sheet_name}", section_id=section_id,
            provenance=Provenance(sheet=sheet_name),
        ))
    out.quality.pages_total = len(out.tables)
    out.quality.pages_with_native_text = len(out.tables)
    return out


def parse_csv(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    text = data.decode("utf-8-sig", errors="replace")
    sample_lines = [line for line in text.splitlines()[:50] if line.strip()]
    delim = ","
    for candidate in (",", ";", "\t", "|"):
        counts = [line.count(candidate) for line in sample_lines]
        if counts and counts[0] >= 1 and all(c == counts[0] for c in counts):
            delim = candidate
            break
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(v.strip() for v in r)]
    out = CanonicalDocument(
        document_id=document_id, source_filename=filename, fmt="csv", mime="text/csv",
        metadata={"parser": "csv-stdlib", "delimiter": delim},
        quality=QualityReport(pages_total=1, pages_with_native_text=1),
    )
    if rows:
        table = _rows_to_table(rows, document_id=document_id, sheet=None, title=filename)
        out.tables.append(table)
        out.quality.tables_detected = 1
    return out


def table_payloads(doc: CanonicalDocument) -> list[dict]:
    """Serialization helper: canonical tables enriched for analytics."""
    payloads = []
    for t in doc.tables:
        payloads.append({
            "table_id": t.table_id, "title": t.title, "sheet": t.sheet,
            "pages": t.pages, "headers": t.headers,
            "n_rows": t.n_rows, "n_cols": t.n_cols,
            "footnotes": t.footnotes, "validation_flags": t.validation_flags,
            "column_types": _column_types(t),
            "records": _records(t),
        })
    return payloads
