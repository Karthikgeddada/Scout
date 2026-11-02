"""Chat and intelligence API schemas (conversations, messages, streaming, SSE)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas_core import ORMModel


class ConversationCreate(BaseModel):
    workspace_id: uuid.UUID
    title: str | None = None
    document_ids: list[uuid.UUID] = Field(default_factory=list)


class ConversationOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    profile_id: uuid.UUID
    title: str | None
    mode: str
    document_ids: list
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    document_ids: list[uuid.UUID] | None = None


class MessageOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    task_type: str | None
    citations: list[dict] = []
    created_at: datetime


class ChatStreamRequest(BaseModel):
    workspace_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    query: str = Field(min_length=1, max_length=8000)


# SSE event payloads (server -> client), tagged with `event:` names
class SseDelta(BaseModel):
    text: str


class SseCitation(BaseModel):
    evidence_id: int
    document_id: str | None
    document_title: str | None
    page: int | None
    sheet: str | None
    locator: str | None
    quote: str | None
    valid: bool


class SseDone(BaseModel):
    message_id: str
    task_type: str
    usage: dict = {}
