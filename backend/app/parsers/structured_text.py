"""Plain-text and Markdown parsers.

Markdown: ATX headings build the section tree; fenced code blocks stay whole;
lists, blockquotes and paragraphs become elements with line provenance.
Plain text: paragraph-per-blank-line split with line numbers.
"""

from __future__ import annotations

import re

from app.ingestion.canonical import (
    CanonicalDocument,
    DocumentElement,
    DocumentSection,
    Provenance,
    QualityReport,
    stable_id,
)

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^```(\\w*)")


def parse_markdown(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    text = data.decode("utf-8", errors="replace")
    out = CanonicalDocument(
        document_id=document_id, source_filename=filename, fmt="md", mime="text/markdown",
        metadata={"parser": "structured-text"},
        quality=QualityReport(pages_total=1, pages_with_native_text=1),
    )
    current_section: DocumentSection | None = None
    lines = text.splitlines()
    buffer: list[str] = []
    code_buffer: list[str] | None = None

    def flush(line_no: int) -> None:
        nonlocal buffer
        paragraph = "\n".join(buffer).strip()
        buffer = []
        if not paragraph:
            return
        out.elements.append(DocumentElement(
            element_id=stable_id(document_id, "el", len(out.elements)),
            kind=("list_item" if _LIST.match(paragraph) else "paragraph"),
            text=paragraph, section_id=current_section.section_id if current_section else None,
            provenance=Provenance(line_start=max(1, line_no - len(paragraph.splitlines()))),
        ))

    for line_no, line in enumerate(lines, start=1):
        if code_buffer is not None:
            code_buffer.append(line)
            if line.strip().startswith("```"):
                out.elements.append(DocumentElement(
                    element_id=stable_id(document_id, "el", len(out.elements)),
                    kind="code", text="\n".join(code_buffer),
                    section_id=current_section.section_id if current_section else None,
                    provenance=Provenance(line_start=line_no - len(code_buffer) + 1),
                ))
                code_buffer = None
            continue
        if line.strip().startswith("```"):
            flush(line_no)
            code_buffer = [line]
            continue
        m = _HEADING.match(line)
        if m:
            flush(line_no)
            section = DocumentSection(
                section_id=stable_id(document_id, "sec", line_no),
                title=m.group(2).strip()[:500], level=len(m.group(1)),
            )
            out.sections.append(section)
            out.elements.append(DocumentElement(
                element_id=section.section_id, kind="heading", text=m.group(2).strip(),
                level=len(m.group(1)), provenance=Provenance(line_start=line_no),
            ))
            current_section = section
            continue
        if not line.strip():
            flush(line_no)
            continue
        buffer.append(line)
    flush(len(lines))
    return out


def parse_txt(data: bytes, *, document_id: str, filename: str) -> CanonicalDocument:
    text = data.decode("utf-8", errors="replace")
    out = CanonicalDocument(
        document_id=document_id, source_filename=filename, fmt="txt", mime="text/plain",
        metadata={"parser": "structured-text"},
        quality=QualityReport(pages_total=1, pages_with_native_text=1),
    )
    for line_no, para in enumerate(text.split("\n\n"), start=1):
        para = para.strip()
        if not para:
            continue
        out.elements.append(DocumentElement(
            element_id=stable_id(document_id, "el", line_no),
            kind="paragraph", text=para,
            provenance=Provenance(line_start=line_no),
        ))
    if not out.elements:
        out.quality.validation_flags.append("empty-document")
    return out
