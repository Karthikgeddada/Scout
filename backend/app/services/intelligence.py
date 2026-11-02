"""Intelligence service: ask (document-bound RAG), summarize, extract, compare.

All four operations share the same retrieval -> context -> Gemini -> citation
validation backbone; only the task framing differs. Answers are document-bound:
with no matching evidence the service reports gaps instead of guessing."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ai.gemini import gemini
from app.db.models_core import UsageRecord
from app.db.models_retrieval import RetrievalRun
from app.rag.citations import (
    CitationValidator,
    ClaimedCitation,
    RetrievedEvidence,
    parse_citation_markers,
)
from app.rag.context import ContextBuilder, render_context
from app.rag.prompt_guard import SYSTEM_PROMPT, neutralize
from app.rag.retrieval import HybridRetriever


class IntelligenceService:
    def __init__(self, retriever: HybridRetriever | None = None):
        self.retriever = retriever or HybridRetriever()

    async def _answer_with_evidence(
        self, session: Session, workspace_id, profile_id, query: str, doc_ids: list[str],
        task_framing: str,
    ) -> dict:
        run = RetrievalRun(
            id=uuid.uuid4(), workspace_id=workspace_id, query=query, task_type="intelligence",
        )
        session.add(run)
        session.flush()

        chunks = self.retriever.retrieve(
            session, query, str(workspace_id), doc_ids or None, persist_run_id=str(run.id)
        )
        blocks = ContextBuilder().build(chunks)
        context = neutralize(render_context(blocks))

        prompt = (
            f"{task_framing}\n\nQUESTION: {query}\n\n"
            f"EVIDENCE:\n{context or '(no matching evidence found)'}"
        )
        answer = await gemini().generate(SYSTEM_PROMPT, prompt)

        registry = {
            b.evidence_id: RetrievedEvidence(
                evidence_id=b.evidence_id, chunk_id=b.chunk_id, document_id=b.document_id,
                document_title=b.document_title, text=b.text, pages=b.pages,
                sheet=b.sheet, locator=b.locator,
            )
            for b in blocks
        }
        validator = CitationValidator(registry)
        citations = []
        for eid in parse_citation_markers(answer):
            check = validator.validate(ClaimedCitation(evidence_id=eid))
            if check.verified is not None:
                v = check.verified
                citations.append({
                    "evidence_id": v.evidence_id, "document_id": v.document_id,
                    "document_title": v.document_title, "page": v.page,
                    "sheet": v.sheet, "locator": v.locator, "quote": v.quote,
                    "valid": True, "rejection_reason": None,
                })
            else:
                citations.append({
                    "evidence_id": eid, "valid": False, "rejection_reason": check.reason,
                })

        session.add(UsageRecord(workspace_id=workspace_id, profile_id=profile_id, kind="intelligence"))
        session.commit()
        return {
            "answer": answer,
            "task_type": "intelligence",
            "citations": citations,
            "evidence": [
                {"evidence_id": b.evidence_id, "document_id": b.document_id,
                 "document_title": b.document_title, "pages": b.pages, "sheet": b.sheet,
                 "locator": b.locator}
                for b in blocks
            ],
            "computed": None,
        }

    async def ask(self, session, req, profile_id) -> dict:
        framing = "Answer the question strictly from the evidence. Cite every claim as [E#]."
        return await self._answer_with_evidence(
            session, req.workspace_id, profile_id, req.query,
            [str(d) for d in req.document_ids], framing,
        )

    async def summarize(self, session, req, profile_id) -> dict:
        styles = {
            "overview": "Produce a structured overview summary with the main themes.",
            "executive": "Produce a concise executive summary: key facts, figures, risks.",
            "section": "Summarize each major section in order with its key points.",
            "multi": "Summarize each document, then highlight what they share and differ on.",
        }
        framing = styles.get(req.style, styles["overview"]) + " Cite every claim as [E#]."
        return await self._answer_with_evidence(
            session, req.workspace_id, profile_id, f"Summarize (style: {req.style}).",
            [str(d) for d in req.document_ids], framing,
        )

    async def extract(self, session, req, profile_id) -> dict:
        fields = ", ".join(req.fields)
        framing = (
            f"Extract all occurrences of: {fields}. Return a structured list where "
            "every item cites its evidence block as [E#]. Include nothing not in evidence."
        )
        return await self._answer_with_evidence(
            session, req.workspace_id, profile_id,
            f"Extract fields: {fields}", [str(d) for d in req.document_ids], framing,
        )

    async def compare(self, session, req, profile_id) -> dict:
        focus = req.focus or "all substantive content"
        framing = (
            "Compare the selected documents. Organize by shared points then differences. "
            f"Focus on: {focus}. Attribute every point to its document via [E#] citations; "
            "surface conflicting figures instead of silently resolving them."
        )
        return await self._answer_with_evidence(
            session, req.workspace_id, profile_id,
            f"Compare documents focusing on: {focus}",
            [str(d) for d in req.document_ids], framing,
        )
