"""Report service: multi-document report generation with citations, exported to
PDF (reportlab) or DOCX (python-docx) and stored privately via Supabase Storage."""

from __future__ import annotations

import io
import uuid

from sqlalchemy.orm import Session

from app.ai.gemini import gemini
from app.core.errors import UpstreamError
from app.core.observability import get_logger
from app.db.models_chat import GeneratedArtifact
from app.db.models_core import UsageRecord
from app.rag.context import ContextBuilder, render_context
from app.rag.prompt_guard import SYSTEM_PROMPT, neutralize
from app.rag.retrieval import HybridRetriever
from app.services.storage import SupabaseStorage

log = get_logger("reports")

REPORT_BRIEF = """You are writing a professional multi-document report. Structure:
1. Title and purpose. 2. Executive summary. 3. Findings by theme, each citing
its evidence blocks as [E#]. 4. Conflicts between sources, if any. 5. Gaps —
what the documents do not cover. Use exact figures from evidence only."""


class ReportService:
    def __init__(self, retriever: HybridRetriever | None = None, storage: SupabaseStorage | None = None):
        self.retriever = retriever or HybridRetriever()
        self.storage = storage or SupabaseStorage()

    async def generate(self, session: Session, req, profile_id) -> GeneratedArtifact:
        artifact = GeneratedArtifact(
            id=uuid.uuid4(), workspace_id=req.workspace_id, profile_id=profile_id,
            kind="report", title=req.title, document_ids=[str(d) for d in req.document_ids],
            format=req.format, status="generating", content={},
        )
        session.add(artifact)
        session.flush()

        doc_ids = [str(d) for d in req.document_ids]
        chunks = self.retriever.retrieve(session, req.title, str(req.workspace_id), doc_ids)
        blocks = ContextBuilder().build(chunks)
        context = neutralize(render_context(blocks))

        prompt = (
            f"REPORT TITLE: {req.title}\n"
            + (f"USER BRIEF: {req.brief}\n" if req.brief else "")
            + f"EVIDENCE:\n{context or '(no matching evidence found)'}"
        )
        body = await gemini().generate(REPORT_BRIEF + "\n\n" + SYSTEM_PROMPT, prompt)

        citation_count = body.count("[E")
        artifact.content = {"body": body, "evidence": [
            {"evidence_id": b.evidence_id, "document_id": b.document_id,
             "document_title": b.document_title, "pages": b.pages, "sheet": b.sheet,
             "locator": b.locator}
            for b in blocks
        ]}
        artifact.citation_count = citation_count
        session.add(UsageRecord(workspace_id=req.workspace_id, profile_id=profile_id, kind="report"))

        # Render export bytes and store privately
        try:
            if req.format == "pdf":
                data = self._render_pdf(req.title, body, blocks)
            else:
                data = self._render_docx(req.title, body, blocks)
        except ImportError as exc:
            log.warning("export_lib_missing", error=str(exc))
            artifact.status = "ready"
            session.commit()
            return artifact

        storage_path = f"{req.workspace_id}/reports/{artifact.id}.{req.format}"
        self._upload(storage_path, data, req.format)
        artifact.storage_path = storage_path
        artifact.status = "ready"
        session.commit()
        return artifact

    def _upload(self, storage_path: str, data: bytes, fmt: str) -> None:
        import httpx

        from app.core.config import get_settings
        from app.core.errors import StorageError

        s = get_settings()
        key = s.supabase_service_role_key
        if not key:
            raise StorageError("service role key not configured")
        mime = "application/pdf" if fmt == "pdf" else (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{s.supabase_url}/storage/v1/object/{s.storage_bucket}/{storage_path}",
                headers={"Authorization": f"Bearer {key}", "apikey": key,
                         "Content-Type": mime, "x-upsert": "true"},
                content=data,
            )
        if resp.status_code >= 400:
            raise StorageError(f"report upload failed: {resp.text}")

    @staticmethod
    def _render_pdf(title: str, body: str, blocks) -> bytes:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, title=title)
        styles = getSampleStyleSheet()
        story = [Paragraph(title.replace("<", "&lt;"), styles["Title"]), Spacer(1, 12)]
        for para in body.split("\n\n"):
            if para.strip():
                story.append(Paragraph(para.strip().replace("<", "&lt;").replace("\n", "<br/>"), styles["BodyText"]))
                story.append(Spacer(1, 8))
        doc.build(story)
        return buf.getvalue()

    @staticmethod
    def _render_docx(title: str, body: str, blocks) -> bytes:
        from docx import Document

        d = Document()
        d.add_heading(title, 0)
        for para in body.split("\n\n"):
            if para.strip():
                d.add_paragraph(para.strip())
        buf = io.BytesIO()
        d.save(buf)
        return buf.getvalue()

    def download_url(self, artifact: GeneratedArtifact) -> str:
        if not artifact.storage_path:
            raise UpstreamError("report has no stored file")
        return self.storage.create_signed_download_url(artifact.storage_path, expires_seconds=600)
