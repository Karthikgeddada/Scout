"""Core API schemas: users, workspaces, shared envelopes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 20


class UserOut(ORMModel):
    id: uuid.UUID
    email: str | None
    display_name: str | None
    avatar_url: str | None
    preferences: dict
    created_at: datetime


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    preferences: dict | None = None


class WorkspaceOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    created_at: datetime


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class WorkspaceMemberOut(ORMModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    role: str
    display_name: str | None = None
    email: str | None = None
