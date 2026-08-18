from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    owner_user_id: uuid.UUID
    owner_email: str
    is_own: bool


class ConversationDetail(ConversationSummary):
    pass


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime


class SendMessageIn(BaseModel):
    content: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    email: str
    role: str
    branch_id: uuid.UUID | None
    branch_name: str | None
    permissions: list[str]
