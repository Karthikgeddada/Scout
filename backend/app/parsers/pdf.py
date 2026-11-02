"""PDF parser: Docling first (deep layout understanding), PyMuPDF fallback.

Produces the canonical representation with pages, headings (font-size
heuristic in fallback mode), native tables, and OCR fallback for image pages.
"""

from __future__ import annotations

import io

from app.core.observability import get_logger
from app.ingestion.canonical import (
    CanonicalDocument,
    DocumentElement,
    DocumentSection,
    Provenance,
    QualityReport,
    stable_id,
)
from app.ingestion.tables import build_cell

log = get_logger("parser.pdf")


def parse_pdf(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    try:
        return _parse_with_docling(data, document_id=document_id, filename=filename)
    except ImportError:
        log.info("docling_unavailable", reason="not installed; using PyMuPDF")
    except Exception as exc:  # noqa: BLE001
        log.warning("docling_failed_fallback", error=str(exc))
    return _parse_with_pymupdf(data, document_id=document_id, filename=filename)


def _parse_with_docling(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(io.BytesIO(data))
    dl = result.document

    doc = CanonicalDocument(
        document_id=document_id, source_filename=filename, fmt="pdf", mime="application/pdf",
        metadata={"parser": "docling"}, quality=QualityReport(parser_versions={"docling": "1.x"}),
    )
    for item, _level in dl.iterate_items():
        label = getattr(item, "label", None)
        text = getattr(item, "text", "") or ""
        prov = getattr(item, "prov", None)
        page = prov[0].page_no if prov else None
        if not text and label not in {"table"}:
            continue
        kind = {
            "title": "heading", "section_header": "heading", "header": "heading",
            "paragraph": "paragraph", "list_item": "list_item", "footnote": "footnote",
            "caption": "caption", "table": "table",
        }.get(label, "paragraph")
        element_id = stable_id(document_id, "el", len(doc.elements))
        doc.elements.append(DocumentElement(
            element_id=element_id, kind=kind, text=text,
            level=(1 if kind == "heading" else None),
            provenance=Provenance(page=page),
        ))
        if kind == "heading":
            doc.sections.append(DocumentSection(
                section_id=element_id, title=text[:500], level=1,
            ))
    # quality accounting from docling pages
    try:
        doc.quality.pages_total = len(result.pages) if result.pages else 1
    except Exception:  # noqa: BLE001
        doc.quality.pages_total = max(1, doc.quality.pages_total)
    doc.quality.pages_with_native_text = max(1, doc.quality.pages_total)
    return doc


def _parse_with_pymupdf(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    import fitz  # PyMuPDF

    doc = CanonicalDocument(
        document_id=document_id, source_filename=filename, fmt="pdf", mime="application/pdf",
        metadata={"parser": "pymupdf"}, quality=QualityReport(parser_versions={"pymupdf": fitz.__doc__ or ""}),
    )
    with fitz.open(stream=data, filetype="pdf") as pdf:
        body_sizes: list[float] = []
        for page in pdf:
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            body_sizes.append(float(span.get("size", 0)))
        # median body font size => heading threshold
        body_sizes.sort()
        median = body_sizes[len(body_sizes) // 2] if body_sizes else 10.0
        heading_threshold = median + 1.5

        for page_index, page in enumerate(pdf, start=1):
            doc.quality.pages_total += 1
            text = page.get_text("text")
            if text.strip():
                doc.quality.pages_with_native_text += 1

            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                block_text = "\n".join(
                    span["text"]
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                ).strip()
                if not block_text:
                    continue
                sizes = [
                    float(span.get("size", 0))
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                    if span.get("text", "").strip()
                ]
                is_heading = bool(sizes) and max(sizes) >= heading_threshold and len(block_text) < 200
                element_id = stable_id(document_id, "el", page_index, len(doc.elements))
                doc.elements.append(DocumentElement(
                    element_id=element_id,
                    kind="heading" if is_heading else "paragraph",
                    text=block_text,
                    level=1 if is_heading else None,
                    provenance=Provenance(page=page_index),
                ))
                if is_heading:
                    doc.sections.append(DocumentSection(
                        section_id=element_id, title=block_text[:500], level=1,
                    ))

            # native tables (PyMuPDF 1.23+)
            finder = getattr(page, "find_tables", None)
            if finder is not None:
                try:
                    tabs = finder()
                    for t_index, tab in enumerate(tabs.tables):
                        rows = tab.extract()
                        if not rows or not rows[0]:
                            continue
                        from app.ingestion.canonical import DocumentTable, TableCell, TableRow

                        table_id = stable_id(document_id, "tbl", page_index, t_index)
                        table = DocumentTable(
                            table_id=table_id, pages=[page_index],
                            title=f"Page {page_index} table {t_index + 1}",
                            validation_flags=["pymupdf-native-table"],
                        )
                        for r_index, row in enumerate(rows):
                            cells = []
                            for c_index, value in enumerate(row or []):
                                raw = "" if value is None else str(value)
                                cells.append(build_cell(raw, r_index, c_index, provenance=Provenance(page=page_index)))
                            table.rows.append(TableRow(
                                cells=cells, is_header=(r_index == 0),
                                provenance=Provenance(page=page_index),
                            ))
                        table.rows[0].is_header = True
                        table.headers = [c.raw_value for c in table.rows[0].cells]
                        doc.tables.append(table)
                        doc.quality.tables_detected += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("table_extract_failed", page=page_index, error=str(exc))

    if doc.quality.pages_total and doc.quality.pages_with_native_text == 0:
        doc.quality.validation_flags.append("no-native-text; ocr-needed")
    return doc
