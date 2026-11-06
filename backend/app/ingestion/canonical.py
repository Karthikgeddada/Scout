"""Canonical document representation.

Every parser (PDF/DOCX/XLSX/CSV/TXT/MD/images) converges on these types, so the
RAG engine never depends on a specific parser. Serializable to/from JSON.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


def stable_id(*parts: Any) -> str:
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]


@dataclass
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Provenance:
    """Where a piece of content came from inside the original document."""

    page: int | None = None          # 1-based page
    sheet: str | None = None         # spreadsheet sheet name
    locator: str | None = None        # cell range (A1:C10) or anchor heading
    bbox: BoundingBox | None = None
    line_start: int | None = None    # TXT/MD line numbers


@dataclass
class TableCell:
    raw_value: str                       # exactly as extracted; never modified
    normalized_value: str | None = None  # canonicalized (numbers, dates)
    data_type: str = "text"               # text|number|currency|percent|date|bool|empty
    unit: str | None = None
    currency: str | None = None
    is_negative: bool = False
    decimal_value: str | None = None      # Decimal-safe string for finance math
    row_index: int = 0
    col_index: int = 0
    provenance: Provenance | None = None


@dataclass
class TableRow:
    cells: list[TableCell] = field(default_factory=list)
    is_header: bool = False
    provenance: Provenance | None = None


@dataclass
class DocumentTable:
    table_id: str
    title: str | None = None
    headers: list[str] = field(default_factory=list)  # normalized merged headers
    rows: list[TableRow] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    sheet: str | None = None
    footnotes: list[str] = field(default_factory=list)
    validation_flags: list[str] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return len([r for r in self.rows if not r.is_header])

    @property
    def n_cols(self) -> int:
        return max((len(r.cells) for r in self.rows), default=0)


@dataclass
class DocumentElement:
    element_id: str
    kind: str  # heading|paragraph|list_item|table|caption|footnote|figure|code|hyperlink|ocr_text
    text: str
    level: int | None = None            # heading depth / list nesting
    section_id: str | None = None
    table_id: str | None = None        # when kind == "table" (ref) or a caption for it
    provenance: Provenance | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentSection:
    section_id: str
    title: str
    level: int
    parent_id: str | None = None
    element_ids: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    pages_total: int = 0
    pages_with_native_text: int = 0
    pages_ocr: int = 0
    pages_empty: int = 0
    tables_detected: int = 0
    validation_flags: list[str] = field(default_factory=list)
    parser_versions: dict[str, str] = field(default_factory=dict)

    @property
    def text_coverage(self) -> float:
        denom = self.pages_total or 1
        return round((self.pages_with_native_text + self.pages_ocr) / denom, 4)

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form (dataclass fields only — text_coverage stays a
        derived property so from_dict(**to_dict()) round-trips cleanly)."""
        return {
            "pages_total": self.pages_total,
            "pages_with_native_text": self.pages_with_native_text,
            "pages_ocr": self.pages_ocr,
            "pages_empty": self.pages_empty,
            "tables_detected": self.tables_detected,
            "validation_flags": self.validation_flags,
            "parser_versions": self.parser_versions,
        }


@dataclass
class CanonicalDocument:
    document_id: str
    source_filename: str
    fmt: str
    mime: str
    metadata: dict[str, Any] = field(default_factory=dict)
    sections: list[DocumentSection] = field(default_factory=list)
    elements: list[DocumentElement] = field(default_factory=list)
    tables: list[DocumentTable] = field(default_factory=list)
    quality: QualityReport = field(default_factory=QualityReport)

    def table_by_id(self, table_id: str) -> DocumentTable | None:
        return next((t for t in self.tables if t.table_id == table_id), None)

    def element_by_id(self, element_id: str) -> DocumentElement | None:
        return next((e for e in self.elements if e.element_id == element_id), None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CanonicalDocument":
        doc = cls(
            document_id=payload["document_id"],
            source_filename=payload["source_filename"],
            fmt=payload["fmt"],
            mime=payload["mime"],
            metadata=payload.get("metadata", {}),
        )
        for s in payload.get("sections", []):
            doc.sections.append(DocumentSection(**s))
        for e in payload.get("elements", []):
            e["provenance"] = _provenance_from_dict(e.get("provenance"))
            doc.elements.append(DocumentElement(**e))
        for t in payload.get("tables", []):
            rows = []
            for r in t.get("rows", []):
                cells = []
                for c in r.get("cells", []):
                    c["provenance"] = _provenance_from_dict(c.get("provenance"))
                    cells.append(TableCell(**c))
                r["cells"] = cells
                r["provenance"] = _provenance_from_dict(r.get("provenance"))
                rows.append(TableRow(**r))
            t["rows"] = rows
            doc.tables.append(DocumentTable(**t))
        doc.quality = QualityReport(**payload.get("quality", {}))
        return doc


def _provenance_from_dict(data: dict[str, Any] | None) -> Provenance | None:
    if data is None:
        return None
    bbox = data.get("bbox")
    if bbox is not None:
        data["bbox"] = BoundingBox(**bbox)
    return Provenance(**data)
