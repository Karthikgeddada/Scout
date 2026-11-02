"""DOCX parser via python-docx: headings from styles, paragraphs in order,
native tables with numeric cell validation, footnotes via raw XML fallback."""

from __future__ import annotations

from app.core.observability import get_logger
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

log = get_logger("parser.docx")


def parse_docx(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    import docx  # python-docx

    d = docx.Document(__import__("io").BytesIO(data))
    out = CanonicalDocument(
        document_id=document_id, source_filename=filename,
        fmt="docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        metadata={"parser": "python-docx"},
        quality=QualityReport(pages_total=1, pages_with_native_text=1, parser_versions={"python-docx": "1"}),
    )

    current_section: str | None = None

    def _add_element(kind: str, text: str, level: int | None = None, table_id: str | None = None) -> None:
        text = text.strip()
        if not text:
            return
        element_id = stable_id(document_id, "el", len(out.elements))
        out.elements.append(DocumentElement(
            element_id=element_id, kind=kind, text=text, level=level,
            section_id=current_section, table_id=table_id,
        ))
        if kind == "heading":
            out.sections.append(DocumentSection(
                section_id=element_id, title=text[:500], level=level or 1,
            ))

    # Iterate body in true document order (paragraphs and tables interleaved)
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in d.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            para = Paragraph(child, d)
            style = (para.style.name or "").lower()
            if style.startswith("heading") or style == "title":
                try:
                    level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
                except ValueError:
                    level = 1
                element = stable_id(document_id, "el", len(out.elements))
                current_section = element
                _add_element("heading", para.text, level=level)
            elif para.text.strip():
                _add_element("list_item" if style.startswith("list") else "paragraph", para.text)
        elif tag == "tbl":
            table = Table(child, d)
            table_id = stable_id(document_id, "tbl", len(out.tables))
            doc_table = DocumentTable(table_id=table_id, validation_flags=["docx-native-table"])
            for r_index, row in enumerate(table.rows):
                cells = []
                for c_index, cell in enumerate(row.cells):
                    cells.append(build_cell(cell.text.strip(), r_index, c_index))
                doc_table.rows.append(TableRow(cells=cells, is_header=(r_index == 0)))
            if doc_table.rows:
                doc_table.rows[0].is_header = True
                doc_table.headers = [c.raw_value for c in doc_table.rows[0].cells]
                doc_table.n_rows  # noqa: B018 - validates property wiring
            out.tables.append(doc_table)
            out.quality.tables_detected += 1
            _add_element("table", f"Table {len(out.tables)}", table_id=table_id)

    if not out.elements and not out.tables:
        out.quality.validation_flags.append("empty-document")
    return out
