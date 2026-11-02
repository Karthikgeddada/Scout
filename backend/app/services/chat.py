"""Chat service: conversations, streaming RAG turns with citations, persistence."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.ai.gemini import gemini
from app.core.observability import get_logger
from app.db.models_chat import Citation, Conversation, Message
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
from app.rag.router import QueryRouter

log = get_logger("chat")

class ChatService:
    def __init__(self, retriever: HybridRetriever | None = None):
        self.retriever = retriever or HybridRetriever()
        self.router = QueryRouter()

    def get_or_create_conversation(self, session, workspace_id, profile_id, payload) -> Conversation:
        if payload.conversation_id:
            conv = session.get(Conversation, payload.conversation_id)
            if conv is None or conv.workspace_id != workspace_id:
                raise KeyError("conversation not found")
            return conv
        conv = Conversation(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            profile_id=profile_id,
            title=(payload.query[:80] if payload.query else None),
            mode="chat",
            document_ids=[str(d) for d in payload.document_ids],
        )
        session.add(conv)
        session.flush()
        return conv

    async def stream_turn(
        self, session: Session, workspace_id: uuid.UUID, profile_id: uuid.UUID, payload
    ) -> AsyncIterator[dict]:
        """Yield SSE-ready dicts: {"type": "delta"|"done"|"error"}."""
        conv = self.get_or_create_conversation(session, workspace_id, profile_id, payload)
        session.add(Message(id=uuid.uuid4(), conversation_id=conv.id, role="user", content=payload.query))

        # 1. Deterministic query plan (task type + possible exact-phrase route)
        doc_ids = [str(d) for d in (payload.document_ids or [])]
        plan = self.router.plan(payload.query, document_ids=doc_ids)

        run = RetrievalRun(
            id=uuid.uuid4(), workspace_id=workspace_id, conversation_id=conv.id,
            query=payload.query, task_type=plan.primary.value,
        )
        session.add(run)
        session.flush()

        # 2. Hybrid retrieval, scoped to selected documents (or the conversation's)
        scope = doc_ids or [str(d) for d in conv.document_ids]
        chunks = self.retriever.retrieve(
            session, payload.query, str(workspace_id), scope or None,
            exact_phrase=plan.exact_phrase, persist_run_id=str(run.id),
        )

        # 3. Budgeted context construction + injection hardening
        blocks = ContextBuilder().build(chunks)
        context = neutralize(render_context(blocks))
        prompt = (
            f"QUESTION: {payload.query}\n\n"
            f"TASK TYPE: {plan.primary.value}\n\n"
            f"EVIDENCE:\n{context}"
        )

        # 4. Stream the answer
        full_answer: list[str] = []
        async for delta in gemini().stream(SYSTEM_PROMPT, prompt):
            full_answer.append(delta)
            yield {"type": "delta", "text": delta}
        answer = "".join(full_answer)

        # 5. Server-side citation validation against the evidence registry
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
                    "evidence_id": eid, "document_id": None, "document_title": None,
                    "page": None, "sheet": None, "locator": None, "quote": None,
                    "valid": False, "rejection_reason": check.reason,
                })

        msg = Message(
            id=uuid.uuid4(), conversation_id=conv.id, role="assistant", content=answer,
            task_type=plan.primary.value, retrieval_run_id=run.id,
            metadata_={"citations": citations, "context_blocks": len(blocks)},
        )
        session.add(msg)
        session.flush()
        for c in citations:
            session.add(Citation(
                id=uuid.uuid4(), message_id=msg.id, conversation_id=conv.id,
                workspace_id=workspace_id, evidence_id=c["evidence_id"],
                document_id=c["document_id"], document_title=c["document_title"],
                page=c["page"], sheet=c["sheet"], locator=c["locator"], quote=c["quote"],
                valid=c["valid"], rejection_reason=c["rejection_reason"],
            ))
        session.add(UsageRecord(workspace_id=workspace_id, profile_id=profile_id, kind="chat_turn"))
        session.commit()

        yield {
            "type": "done",
            "message_id": str(msg.id),
            "conversation_id": str(conv.id),
            "task_type": plan.primary.value,
            "citations": citations,
        }
