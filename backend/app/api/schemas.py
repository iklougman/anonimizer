from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime


class SendMessageIn(BaseModel):
    content: str
