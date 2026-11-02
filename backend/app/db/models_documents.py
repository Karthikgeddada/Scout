"""Document domain: documents, versions, sections, elements, tables, rows, cells."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models_core import pk
from app.db.session import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_ws_status", "workspace_id", "status"),
        Index("ix_documents_ws_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    source_filename: Mapped[str] = mapped_column(String(300))
    storage_path: Mapped[str] = mapped_column(Text)  # private Supabase Storage object path
    fmt: Mapped[str] = mapped_column(String(20))     # detected canonical format
    mime: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    # pending|uploaded|queued|processing|ready|partial|failed|deleted
    version: Mapped[int] = mapped_column(Integer, default=1)
    collection: Mapped[str | None] = mapped_column(String(200), index=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (Index("ix_doc_versions", "document_id", "version"),)

    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    canonical: Mapped[dict] = mapped_column(JSONB)  # CanonicalDocument.to_dict()
    quality: Mapped[dict] = mapped_column(JSONB, default=dict)
    parser_versions: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentSection(Base):
    __tablename__ = "document_sections"
    __table_args__ = (Index("ix_sections_doc", "document_id"),)

    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    section_id: Mapped[str] = mapped_column(String(40), index=True)  # canonical id, stable per doc
    title: Mapped[str] = mapped_column(String(500))
    level: Mapped[int] = mapped_column(Integer)
    parent_section_id: Mapped[str | None] = mapped_column(String(40))
    path: Mapped[str | None] = mapped_column(Text)  # "1.2.3 > Title"


class DocumentElement(Base):
    __tablename__ = "document_elements"
    __table_args__ = (Index("ix_elements_doc_kind", "document_id", "kind"),)

    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    section_id: Mapped[str | None] = mapped_column(String(40))
    element_id: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    level: Mapped[int | None] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    sheet: Mapped[str | None] = mapped_column(String(200))
    locator: Mapped[str | None] = mapped_column(String(100))
    bbox: Mapped[dict | None] = mapped_column(JSONB)  # {x0,y0,x1,y1}
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class DocumentTable(Base):
    __tablename__ = "document_tables"
    __table_args__ = (Index("ix_tables_doc", "document_id"),)

    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    table_id: Mapped[str] = mapped_column(String(40))
    title: Mapped[str | None] = mapped_column(String(300))
    sheet: Mapped[str | None] = mapped_column(String(200))
    pages: Mapped[list] = mapped_column(JSONB, default=list)
    headers: Mapped[list] = mapped_column(JSONB, default=list)
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    n_cols: Mapped[int] = mapped_column(Integer, default=0)
    footnotes: Mapped[list] = mapped_column(JSONB, default=list)
    validation_flags: Mapped[list] = mapped_column(JSONB, default=list)


class DocumentTableRow(Base):
    __tablename__ = "document_table_rows"
    __table_args__ = (Index("ix_rows_table", "table_id"),)

    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    table_db_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_tables.id", ondelete="CASCADE"))
    row_index: Mapped[int] = mapped_column(Integer)
    is_header: Mapped[bool] = mapped_column(Boolean, default=False)
    page: Mapped[int | None] = mapped_column(Integer)


class DocumentTableCell(Base):
    __tablename__ = "document_table_cells"
    __table_args__ = (Index("ix_cells_row", "row_db_id"),)

    id: Mapped[uuid.UUID] = pk()
    row_db_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_table_rows.id", ondelete="CASCADE"))
    row_index: Mapped[int] = mapped_column(Integer)
    col_index: Mapped[int] = mapped_column(Integer)
    raw_value: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    data_type: Mapped[str] = mapped_column(String(20), default="text")
    unit: Mapped[str | None] = mapped_column(String(20))
    currency: Mapped[str | None] = mapped_column(String(8))
    is_negative: Mapped[bool] = mapped_column(Boolean, default=False)
    decimal_value: Mapped[str | None] = mapped_column(String(100))  # Decimal-safe string
    numeric_score: Mapped[float | None] = mapped_column(Float)     # extraction confidence
