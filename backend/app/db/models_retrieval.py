"""Retrieval domain: chunks, embeddings (pgvector), retrieval runs/candidates."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models_core import pk
from app.db.session import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_chunks_doc", "document_id"),
        Index("ix_chunks_ws_kind", "workspace_id", "kind"),
    )

    id: Mapped[uuid.UUID] = pk()
    chunk_id: Mapped[str] = mapped_column(String(40), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(20))  # text|code|table|table_rows
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    parent_section_id: Mapped[str | None] = mapped_column(String(40))
    section_title: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[list] = mapped_column(JSONB, default=list)
    sheet: Mapped[str | None] = mapped_column(String(200))
    locator: Mapped[str | None] = mapped_column(String(100))
    table_id: Mapped[str | None] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # tsv (tsvector) column is created only via SQL migration for FTS.


class DocumentEmbedding(Base):
    __tablename__ = "document_embeddings"
    __table_args__ = (Index("ix_emb_chunk", "chunk_db_id"),)

    id: Mapped[uuid.UUID] = pk()
    chunk_db_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_chunks.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    model_id: Mapped[str] = mapped_column(String(120))
    model_version: Mapped[str | None] = mapped_column(String(80))
    dim: Mapped[int] = mapped_column(Integer)
    # actual vector column (pgvector) is created via SQL migration
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetrievalRun(Base):
    __tablename__ = "retrieval_runs"
    __table_args__ = (Index("ix_runs_ws_time", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    query: Mapped[str] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(30))
    lexical_count: Mapped[int] = mapped_column(Integer, default=0)
    dense_count: Mapped[int] = mapped_column(Integer, default=0)
    reranked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetrievalCandidate(Base):
    __tablename__ = "retrieval_candidates"
    __table_args__ = (Index("ix_cand_run_rank", "run_id", "rank"),)

    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("retrieval_runs.id", ondelete="CASCADE"))
    chunk_db_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_chunks.id", ondelete="SET NULL"))
    rank: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))  # lexical|dense|fused|rerank
    score: Mapped[float | None] = mapped_column(Float)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
