"""Document service: upload flow, listing, deletion, status, reprocessing.

Upload flow: signed URL -> client PUT -> complete-upload (object verified) ->
document row + processing job -> in-process background ingestion task.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, RequestValidationError
from app.db.models_chat import Conversation
from app.db.models_core import AuditLog
from app.db.models_documents import Document, DocumentVersion
from app.db.models_jobs import ProcessingJob
from app.services.storage import SupabaseStorage


class DocumentService:
    def __init__(self, storage: SupabaseStorage | None = None) -> None:
        self.storage = storage or SupabaseStorage()
        self.settings = get_settings()

    # ---- upload ----

    def create_upload(self, session: Session, workspace_id: uuid.UUID, profile_id: uuid.UUID, req) -> dict:
        if req.size_bytes > self.settings.upload_max_bytes:
            raise RequestValidationError(
                f"file too large (max {self.settings.upload_max_bytes} bytes)"
            )
        doc_id = uuid.uuid4()
        storage_path = f"{workspace_id}/{doc_id}/{req.filename}"
        signed = self.storage.create_signed_upload_url(storage_path)
        doc = Document(
            id=doc_id,
            workspace_id=workspace_id,
            title=req.filename.rsplit("/", 1)[-1],
            source_filename=req.filename,
            storage_path=storage_path,
            fmt="unknown",  # set by worker after signature detection
            mime=req.mime or "application/octet-stream",
            size_bytes=req.size_bytes,
            status="pending",
            collection=req.collection,
            uploaded_by=profile_id,
        )
        session.add(doc)
        session.flush()
        session.add(AuditLog(workspace_id=workspace_id, actor_profile_id=profile_id,
                             action="document.upload_url", target_type="document", target_id=str(doc_id)))
        return {
            "document_id": str(doc_id),
            "upload_url": signed["url"],
            "storage_path": storage_path,
            "expires_in": 900,
        }

    def complete_upload(self, session: Session, document_id: uuid.UUID, profile_id: uuid.UUID) -> tuple[Document, str]:
        doc = session.get(Document, document_id)
        if doc is None:
            raise NotFoundError("document not found")
        if doc.status not in {"pending", "failed"}:
            raise ConflictError(f"document already {doc.status}")
        info = self.storage.stat(doc.storage_path)
        if info is None:
            raise NotFoundError("uploaded object not found in storage")
        doc.status = "queued"
        job = ProcessingJob(
            id=uuid.uuid4(),
            workspace_id=doc.workspace_id,
            document_id=doc.id,
            job_type="ingest",
            status="queued",
            idempotency_key=f"ingest:{doc.id}:v{doc.version}",
        )
        session.add(job)
        session.flush()
        session.add(AuditLog(workspace_id=doc.workspace_id, actor_profile_id=profile_id,
                             action="document.upload_complete", target_type="document", target_id=str(doc.id)))
        return doc, str(job.id)

    # ---- queries ----

    def list_documents(self, session: Session, params) -> tuple[list[Document], int]:
        q = select(Document).where(Document.workspace_id == params.workspace_id)
        if params.status:
            q = q.where(Document.status == params.status)
        if params.collection:
            q = q.where(Document.collection == params.collection)
        if params.q:
            like = f"%{params.q}%"
            q = q.where(or_(Document.title.ilike(like), Document.source_filename.ilike(like)))
        total = session.scalar(
            select(func.count()).select_from(Document).where(q.whereclause)
        ) or 0
        q = q.order_by(Document.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)
        return list(session.scalars(q)), int(total)

    def get_document(self, session: Session, document_id: uuid.UUID) -> Document:
        doc = session.get(Document, document_id)
        if doc is None:
            raise NotFoundError("document not found")
        return doc

    def status(self, session: Session, document_id: uuid.UUID) -> dict:
        doc = self.get_document(session, document_id)
        job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == doc.id)
            .order_by(ProcessingJob.created_at.desc()).limit(1)
        )
        return {
            "document_id": str(doc.id),
            "status": doc.status,
            "stage": job.stage if job else None,
            "progress": job.progress if job else (100 if doc.status == "ready" else 0),
            "job_id": str(job.id) if job else None,
            "error": job.error if job else None,
        }

    def content(self, session: Session, document_id: uuid.UUID) -> dict:
        doc = self.get_document(session, document_id)
        version = session.scalar(
            select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version.desc()).limit(1)
        )
        if version is None:
            raise NotFoundError("document has no processed content yet")
        canonical = version.canonical
        return {
            "document_id": str(doc.id),
            "sections": canonical.get("sections", []),
            "elements": canonical.get("elements", []),
            "tables": canonical.get("tables", []),
            "quality": version.quality,
        }

    def delete(self, session: Session, document_id: uuid.UUID, profile_id: uuid.UUID) -> None:
        doc = self.get_document(session, document_id)
        self.storage.delete(doc.storage_path)
        session.delete(doc)  # child rows cascade (FK ondelete + SQLite pragma)
        session.add(AuditLog(workspace_id=doc.workspace_id, actor_profile_id=profile_id,
                             action="document.delete", target_type="document", target_id=str(document_id),
                             details={"title": doc.title}))

    def reprocess(self, session: Session, document_id: uuid.UUID) -> tuple[Document, str]:
        doc = self.get_document(session, document_id)
        doc.version += 1
        doc.status = "queued"
        job = ProcessingJob(
            workspace_id=doc.workspace_id, document_id=doc.id, job_type="reingest",
            status="queued", idempotency_key=f"ingest:{doc.id}:v{doc.version}",
        )
        session.add(job)
        session.flush()
        return doc, str(job.id)

    # ---- home/recents ----

    def recent(self, session: Session, workspace_id: uuid.UUID, limit: int = 10) -> list[Document]:
        q = (
            select(Document).where(Document.workspace_id == workspace_id)
            .order_by(Document.created_at.desc()).limit(limit)
        )
        return list(session.scalars(q))

    def recent_conversations(self, session: Session, workspace_id: uuid.UUID, limit: int = 10):
        q = (
            select(Conversation).where(Conversation.workspace_id == workspace_id)
            .order_by(Conversation.updated_at.desc()).limit(limit)
        )
        return list(session.scalars(q))
