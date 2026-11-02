"""Intelligence, analytics, reports and job API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas_core import ORMModel


class IntelligenceQueryRequest(BaseModel):
    workspace_id: uuid.UUID
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    query: str = Field(min_length=1, max_length=8000)


class SummarizeRequest(BaseModel):
    workspace_id: uuid.UUID
    document_ids: list[uuid.UUID] = Field(min_length=1)
    style: str = Field(default="overview", pattern="^(overview|executive|section|multi)$")


class ExtractedFact(BaseModel):
    kind: str
    value: str
    citation: dict


class ExtractRequest(BaseModel):
    workspace_id: uuid.UUID
    document_ids: list[uuid.UUID] = Field(min_length=1)
    fields: list[str] = Field(default_factory=lambda: ["names", "dates", "amounts", "organizations"])


class CompareRequest(BaseModel):
    workspace_id: uuid.UUID
    document_ids: list[uuid.UUID] = Field(min_length=2, max_length=5)
    focus: str | None = Field(default=None, max_length=2000)


class IntelligenceResult(BaseModel):
    answer: str
    task_type: str
    citations: list[dict] = []
    evidence: list[dict] = []
    computed: dict | None = None


class AnalyticsQueryRequest(BaseModel):
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    query: str = Field(min_length=1, max_length=2000)


class AnalyticsResult(BaseModel):
    document_id: str
    sheet: str | None
    rows: list[dict]
    provenance: dict
    plan_summary: dict
    explanation: str | None = None


class ReportRequest(BaseModel):
    workspace_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    document_ids: list[uuid.UUID] = Field(min_length=1)
    brief: str | None = Field(default=None, max_length=4000)
    format: str = Field(default="pdf", pattern="^(pdf|docx)$")


class ReportOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    title: str
    document_ids: list
    format: str | None
    status: str
    citation_count: int
    created_at: datetime


class JobOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    progress: int
    stage: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class UsageOut(BaseModel):
    kind: str
    units: int
    month: str


class AnyDict(BaseModel):
    data: dict[str, Any] = {}
