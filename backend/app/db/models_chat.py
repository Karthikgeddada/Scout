"""Chat domain: conversations, messages, citations, generated artifacts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models_core import pk
from app.db.session import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conv_ws_time", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(300))
    mode: Mapped[str] = mapped_column(String(20), default="chat")  # chat|summarize|extract|compare|report
    document_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_msg_conv_order", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(10))  # user|assistant
    content: Mapped[str] = mapped_column(Text)
    task_type: Mapped[str | None] = mapped_column(String(30))
    retrieval_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("retrieval_runs.id", ondelete="SET NULL"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (Index("ix_cit_message", "message_id"),)

    id: Mapped[uuid.UUID] = pk()
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    evidence_id: Mapped[int] = mapped_column(Integer)
    chunk_db_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_chunks.id", ondelete="SET NULL"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    document_title: Mapped[str | None] = mapped_column(String(300))
    page: Mapped[int | None] = mapped_column(Integer)
    sheet: Mapped[str | None] = mapped_column(String(200))
    locator: Mapped[str | None] = mapped_column(String(100))
    quote: Mapped[str | None] = mapped_column(Text)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GeneratedArtifact(Base):
    __tablename__ = "generated_artifacts"
    __table_args__ = (Index("ix_artifacts_ws_time", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))  # report|summary|comparison|extraction
    title: Mapped[str] = mapped_column(String(300))
    document_ids: Mapped[list] = mapped_column(JSON, default=list)
    storage_path: Mapped[str | None] = mapped_column(Text)  # private storage object
    format: Mapped[str | None] = mapped_column(String(10))  # pdf|docx|md
    status: Mapped[str] = mapped_column(String(20), default="pending")
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # structured report body
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
