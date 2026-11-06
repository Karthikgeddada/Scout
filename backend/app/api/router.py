"""SCOUT API v1: all routes under /v1.

Conventions: UUID ids in paths, JSON envelopes with errors:
{"error": {"code", "message", "request_id"}}, cursor-style pagination via page params.
Streaming endpoints use SSE."""

from __future__ import annotations

import json
import uuid as uuid_mod

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_workspace
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.models_chat import Conversation, GeneratedArtifact, Message
from app.db.models_core import Workspace, WorkspaceMember
from app.db.models_documents import Document
from app.db.models_jobs import ProcessingJob
from app.db.session import get_session
from app.ingestion.tasks import run_ingestion
from app.schemas_chat import (
    ChatStreamRequest,
    ConversationCreate,
    ConversationOut,
    MessageOut,
)
from app.schemas_core import (
    Page,
    UserOut,
    UserUpdate,
    WorkspaceCreate,
    WorkspaceMemberOut,
    WorkspaceOut,
)
from app.schemas_documents import (
    CompleteUploadRequest,
    DocumentContentOut,
    DocumentListParams,
    DocumentOut,
    DocumentStatusOut,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.schemas_ops import (
    AnalyticsQueryRequest,
    AnalyticsResult,
    CompareRequest,
    ExtractRequest,
    IntelligenceQueryRequest,
    IntelligenceResult,
    JobOut,
    ReportOut,
    ReportRequest,
    SummarizeRequest,
    UsageOut,
)
from app.services.analytics import AnalyticsService
from app.services.chat import ChatService
from app.services.documents import DocumentService
from app.services.intelligence import IntelligenceService
from app.services.reports import ReportService

router = APIRouter(prefix=get_settings().api_prefix)

documents_svc = DocumentService()
chat_svc = ChatService()
intelligence_svc = IntelligenceService()
analytics_svc = AnalyticsService()
reports_svc = ReportService()


# ---------- users ----------


@router.get("/users/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user)):
    return user.profile


@router.patch("/users/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if payload.display_name is not None:
        user.profile.display_name = payload.display_name
    if payload.preferences is not None:
        user.profile.preferences = payload.preferences
    session.commit()
    return user.profile


# ---------- workspaces ----------


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(
    user: CurrentUser = Depends(get_current_user), session: Session = Depends(get_session)
):
    rows = session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.profile_id == user.profile.id)
        .order_by(Workspace.created_at)
    ).scalars().all()
    return list(rows)


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    slug = payload.name.lower().replace(" ", "-")[:220] + "-" + uuid_mod.uuid4().hex[:8]
    ws = Workspace(id=uuid_mod.uuid4(), name=payload.name, slug=slug,
                   description=payload.description, created_by=user.profile.id)
    session.add(ws)
    session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, profile_id=user.profile.id, role="owner"))
    session.commit()
    return ws


@router.get("/workspaces/{workspace_id}/members", response_model=list[WorkspaceMemberOut])
def workspace_members(ws: Workspace = Depends(require_workspace), session: Session = Depends(get_session)):
    return list(session.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id)))


# ---------- documents ----------


@router.post("/documents/upload-url", response_model=UploadUrlResponse, status_code=201)
def create_upload_url(
    payload: UploadUrlRequest,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(payload.workspace_id), user, session)
    return documents_svc.create_upload(session, payload.workspace_id, user.profile.id, payload)


@router.post("/documents/complete-upload", response_model=DocumentOut)
def complete_upload(
    payload: CompleteUploadRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    doc = session.get(Document, payload.document_id)
    if doc is None:
        raise NotFoundError("document not found")
    require_workspace(str(doc.workspace_id), user, session)
    doc, job_id = documents_svc.complete_upload(session, payload.document_id, user.profile.id)
    session.commit()
    # In-process ingestion (no broker): runs on this same Render service after
    # the response is sent; the task opens its own DB session.
    background_tasks.add_task(run_ingestion, job_id, str(doc.id), str(doc.workspace_id), doc.version)
    return doc


@router.get("/documents", response_model=Page[DocumentOut])
def list_documents(
    workspace_id: uuid_mod.UUID,
    q: str | None = None,
    status: str | None = None,
    collection: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(workspace_id), user, session)
    params = DocumentListParams(workspace_id=workspace_id, q=q, status=status,
                                collection=collection, page=page, page_size=page_size)
    docs, total = documents_svc.list_documents(session, params)
    return Page(items=[DocumentOut.model_validate(d) for d in docs], total=total,
                page=page, page_size=page_size)


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    doc = session.get(Document, document_id)
    if doc is None:
        raise NotFoundError("document not found")
    require_workspace(str(doc.workspace_id), user, session)
    return doc


@router.get("/documents/{document_id}/status", response_model=DocumentStatusOut)
def document_status(
    document_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    doc = session.get(Document, document_id)
    if doc is None:
        raise NotFoundError("document not found")
    require_workspace(str(doc.workspace_id), user, session)
    return documents_svc.status(session, document_id)


@router.get("/documents/{document_id}/content", response_model=DocumentContentOut)
def document_content(
    document_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    doc = session.get(Document, document_id)
    if doc is None:
        raise NotFoundError("document not found")
    require_workspace(str(doc.workspace_id), user, session)
    return documents_svc.content(session, document_id)


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    doc = session.get(Document, document_id)
    if doc is None:
        raise NotFoundError("document not found")
    require_workspace(str(doc.workspace_id), user, session)
    documents_svc.delete(session, document_id, user.profile.id)
    session.commit()


@router.post("/documents/{document_id}/reprocess", response_model=DocumentOut)
def reprocess_document(
    document_id: uuid_mod.UUID,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    doc = session.get(Document, document_id)
    if doc is None:
        raise NotFoundError("document not found")
    require_workspace(str(doc.workspace_id), user, session)
    doc, job_id = documents_svc.reprocess(session, document_id)
    session.commit()
    background_tasks.add_task(run_ingestion, job_id, str(doc.id), str(doc.workspace_id), doc.version)
    return doc


# ---------- conversations ----------


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(
    payload: ConversationCreate,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(payload.workspace_id), user, session)
    conv = Conversation(
        id=uuid_mod.uuid4(), workspace_id=payload.workspace_id, profile_id=user.profile.id,
        title=payload.title, mode="chat", document_ids=[str(d) for d in payload.document_ids],
    )
    session.add(conv)
    session.commit()
    return conv


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    workspace_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(workspace_id), user, session)
    return list(session.scalars(
        select(Conversation).where(Conversation.workspace_id == workspace_id)
        .order_by(Conversation.updated_at.desc()).limit(50)
    ))


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def conversation_messages(
    conversation_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    conv = session.get(Conversation, conversation_id)
    if conv is None:
        raise NotFoundError("conversation not found")
    require_workspace(str(conv.workspace_id), user, session)
    msgs = list(session.scalars(
        select(Message).where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    ))
    out = []
    for m in msgs:
        citations = (m.metadata_ or {}).get("citations", [])
        out.append(MessageOut(id=m.id, conversation_id=m.conversation_id, role=m.role,
                              content=m.content, task_type=m.task_type,
                              citations=citations, created_at=m.created_at))
    return out


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    conv = session.get(Conversation, conversation_id)
    if conv is None:
        raise NotFoundError("conversation not found")
    require_workspace(str(conv.workspace_id), user, session)
    session.delete(conv)
    session.commit()


# ---------- chat (SSE streaming) ----------


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatStreamRequest,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(payload.workspace_id), user, session)

    async def event_stream():
        try:
            async for event in chat_svc.stream_turn(
                session, payload.workspace_id, user.profile.id, payload
            ):
                yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------- intelligence ----------


@router.post("/intelligence/ask", response_model=IntelligenceResult)
async def intelligence_ask(
    payload: IntelligenceQueryRequest,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(payload.workspace_id), user, session)
    return await intelligence_svc.ask(session, payload, user.profile.id)


@router.post("/intelligence/summarize", response_model=IntelligenceResult)
async def intelligence_summarize(
    payload: SummarizeRequest,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(payload.workspace_id), user, session)
    return await intelligence_svc.summarize(session, payload, user.profile.id)


@router.post("/intelligence/extract", response_model=IntelligenceResult)
async def intelligence_extract(
    payload: ExtractRequest,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(payload.workspace_id), user, session)
    return await intelligence_svc.extract(session, payload, user.profile.id)


@router.post("/intelligence/compare", response_model=IntelligenceResult)
async def intelligence_compare(
    payload: CompareRequest,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(payload.workspace_id), user, session)
    return await intelligence_svc.compare(session, payload, user.profile.id)


# ---------- analytics ----------


@router.post("/analytics/query", response_model=AnalyticsResult)
async def analytics_query(
    payload: AnalyticsQueryRequest,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(payload.workspace_id), user, session)
    return await analytics_svc.run(session, payload)


# ---------- reports ----------


@router.post("/reports", response_model=ReportOut, status_code=201)
async def create_report(
    payload: ReportRequest,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(payload.workspace_id), user, session)
    artifact = await reports_svc.generate(session, payload, user.profile.id)
    return artifact


@router.get("/reports", response_model=list[ReportOut])
def list_reports(
    workspace_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(workspace_id), user, session)
    return list(session.scalars(
        select(GeneratedArtifact).where(GeneratedArtifact.workspace_id == workspace_id)
        .order_by(GeneratedArtifact.created_at.desc()).limit(50)
    ))


@router.get("/reports/{report_id}/download")
def download_report(
    report_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artifact = session.get(GeneratedArtifact, report_id)
    if artifact is None:
        raise NotFoundError("report not found")
    require_workspace(str(artifact.workspace_id), user, session)
    return {"url": reports_svc.download_url(artifact)}


# ---------- jobs & usage ----------


@router.get("/jobs/{document_id}", response_model=list[JobOut])
def document_jobs(
    document_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    doc = session.get(Document, document_id)
    if doc is None:
        raise NotFoundError("document not found")
    require_workspace(str(doc.workspace_id), user, session)
    return list(session.scalars(
        select(ProcessingJob).where(ProcessingJob.document_id == document_id)
        .order_by(ProcessingJob.created_at.desc()).limit(20)
    ))


@router.get("/usage", response_model=list[UsageOut])
def usage(
    workspace_id: uuid_mod.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(workspace_id), user, session)
    from app.db.models_core import UsageRecord

    # Portable grouping (SQLite has no date_trunc): aggregate in Python.
    rows = session.execute(
        select(UsageRecord.kind, UsageRecord.created_at, UsageRecord.units)
        .where(UsageRecord.workspace_id == workspace_id)
    ).all()
    grouped: dict[tuple[str, str], int] = {}
    for kind, created_at, units in rows:
        month = created_at.strftime("%Y-%m")
        grouped[(kind, month)] = grouped.get((kind, month), 0) + int(units or 0)
    return [UsageOut(kind=k, month=m, units=u) for (k, m), u in grouped.items()]


# ---------- home / recents ----------


@router.get("/home/recent")
def home_recent(
    workspace_id: uuid_mod.UUID,
    limit: int = Query(default=10, ge=1, le=50),
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    require_workspace(str(workspace_id), user, session)
    docs = documents_svc.recent(session, workspace_id, limit)
    convs = documents_svc.recent_conversations(session, workspace_id, limit)
    return {
        "documents": [DocumentOut.model_validate(d) for d in docs],
        "conversations": [ConversationOut.model_validate(c) for c in convs],
    }
