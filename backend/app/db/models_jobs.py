"""Job domain: durable processing jobs + transactional outbox events.

PostgreSQL is the source of truth for job history; RabbitMQ only transports work."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models_core import pk
from app.db.session import Base


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index("ix_jobs_ws_status", "workspace_id", "status"),
        Index("ix_jobs_doc", "document_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String(40), default="ingest")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    # queued|running|succeeded|partial|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    stage: Mapped[str | None] = mapped_column(String(60))
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(Base):
    """Transactional outbox: broker messages persisted with the DB transaction,
    then dispatched to RabbitMQ with publisher confirms."""

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_pending", "dispatched", "created_at"),)

    id: Mapped[uuid.UUID] = pk()
    topic: Mapped[str] = mapped_column(String(60))  # e.g. ingest.document
    payload: Mapped[dict] = mapped_column(JSON)
    dispatched: Mapped[bool] = mapped_column(Boolean, default=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
