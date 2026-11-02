"""Parser dispatch pipeline: detect real format -> validate -> parse -> canonical.

The pipeline never trusts the extension; detection decides the format, and
every parser converges on the CanonicalDocument representation."""

from __future__ import annotations

import zipfile

from app.core.errors import RequestValidationError
from app.core.observability import get_logger
from app.ingestion.canonical import CanonicalDocument
from app.ingestion.detection import detect_format, validate_upload
from app.parsers.docx import parse_docx
from app.parsers.pdf import parse_pdf
from app.parsers.spreadsheets import parse_csv, parse_xlsx
from app.parsers.structured_text import parse_markdown, parse_txt

log = get_logger("parser.pipeline")


def detect(data: bytes, filename: str) -> str:
    header = data[:8192]
    members: set[str] | None = None
    if header.startswith(b"PK"):
        try:
            members = set(zipfile.ZipFile(__import__("io").BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            members = None
    return detect_format(filename, header, data[:65536], ooxml_members=members)


def parse_document(data: bytes, *, document_id: str, filename: str, declared_format: str | None = None) -> CanonicalDocument:
    """Validate + parse bytes into the canonical representation."""
    detected = detect(data, filename)
    settings = __import__("app.core.config", fromlist=["get_settings"]).get_settings()
    reasons = validate_upload(detected, len(data), settings.upload_max_bytes)
    if reasons:
        raise RequestValidationError("; ".join(reasons))

    log.info("parse", document_id=document_id, fmt=detected.fmt, method=detected.method)

    parser = {
        "pdf": parse_pdf,
        "docx": parse_docx,
        "xlsx": parse_xlsx,
        "csv": parse_csv,
        "md": parse_markdown,
        "txt": parse_txt,
        "pptx": _parse_pptx,
        "png": _parse_image,
        "jpeg": _parse_image,
        "gif": _parse_image,
        "tiff": _parse_image,
        "bmp": _parse_image,
        "webp": _parse_image,
    }.get(detected.fmt)
    if parser is None:
        raise RequestValidationError(f"unsupported format: {detected.fmt}")
    doc = parser(data, document_id=document_id, filename=filename)
    doc.metadata = {**doc.metadata, "detected_format": detected.fmt,
                    "detection_method": detected.method,
                    "detection_confidence": detected.confidence}
    if declared_format and declared_format != detected.fmt:
        doc.metadata["extension_disagreement"] = declared_format
    return doc


def _parse_pptx(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    """PPTX: Docling if available; otherwise slide-text via python-pptx."""
    from app.ingestion.canonical import DocumentElement, DocumentSection, Provenance, QualityReport, stable_id

    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RequestValidationError("pptx parsing requires python-pptx") from exc

    prs = Presentation(__import__("io").BytesIO(data))
    out = CanonicalDocument(
        document_id=document_id, source_filename=filename, fmt="pptx",
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        metadata={"parser": "python-pptx"},
        quality=QualityReport(parser_versions={"python-pptx": "1"}),
    )
    for s_index, slide in enumerate(prs.slides, start=1):
        out.quality.pages_total += 1
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in para.runs).strip()
                    if t:
                        texts.append((bool(para.level), t))
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    texts.append((False, " | ".join(c.text.strip() for c in row.cells)))
        if texts:
            out.quality.pages_with_native_text += 1
            section_id = stable_id(document_id, "sec", s_index)
            title = next((t for lvl, t in texts if lvl), f"Slide {s_index}")
            out.sections.append(DocumentSection(section_id=section_id, title=title[:500], level=1))
            out.elements.append(DocumentElement(
                element_id=section_id, kind="heading", text=title, level=1,
                provenance=Provenance(page=s_index),
            ))
            for _, t in texts:
                out.elements.append(DocumentElement(
                    element_id=stable_id(document_id, "el", s_index, len(out.elements)),
                    kind="paragraph", text=t, section_id=section_id,
                    provenance=Provenance(page=s_index),
                ))
    return out


def _parse_image(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    """Images: OCR via Docling if available; otherwise a clear, actionable error.
    (Images are supported formats, but OCR needs the docling extra installed.)"""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RequestValidationError(
            "image upload accepted, but OCR requires the docling extra (pip install scout[ingestion])"
        ) from exc

    import io

    converter = DocumentConverter()
    result = converter.convert(io.BytesIO(data))
    out = CanonicalDocument(
        document_id=document_id, source_filename=filename, fmt="image",
        mime="", metadata={"parser": "docling-ocr"},
    )
    out.fmt = "image"
    from app.ingestion.canonical import DocumentElement, Provenance, QualityReport, stable_id

    out.quality = QualityReport(pages_total=1, pages_ocr=1)
    for item, _level in result.document.iterate_items():
        text = getattr(item, "text", "") or ""
        if text.strip():
            out.elements.append(DocumentElement(
                element_id=stable_id(document_id, "el", len(out.elements)),
                kind="ocr_text", text=text, provenance=Provenance(page=1),
            ))
    if not out.elements:
        out.quality.validation_flags.append("ocr-no-text")
    return out
