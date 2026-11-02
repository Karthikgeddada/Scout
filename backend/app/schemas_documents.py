"""Document API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas_core import ORMModel


class UploadUrlRequest(BaseModel):
    workspace_id: uuid.UUID
    filename: str = Field(min_length=1, max_length=300)
    size_bytes: int = Field(gt=0)
    mime: str | None = None
    collection: str | None = Field(default=None, max_length=200)


class UploadUrlResponse(BaseModel):
    document_id: uuid.UUID
    upload_url: str
    storage_path: str
    expires_in: int


class CompleteUploadRequest(BaseModel):
    document_id: uuid.UUID


class DocumentOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    source_filename: str
    fmt: str
    mime: str
    size_bytes: int
    status: str
    version: int
    collection: str | None
    created_at: datetime
    updated_at: datetime


class DocumentStatusOut(BaseModel):
    document_id: uuid.UUID
    status: str
    stage: str | None
    progress: int
    job_id: uuid.UUID | None = None
    error: str | None = None


class DocumentContentOut(BaseModel):
    document_id: uuid.UUID
    sections: list[dict]
    elements: list[dict]
    tables: list[dict]
    quality: dict


class DocumentListParams(BaseModel):
    workspace_id: uuid.UUID
    q: str | None = None
    status: str | None = None
    collection: str | None = None
    page: int = 1
    page_size: int = Field(default=20, le=100)
