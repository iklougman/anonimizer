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


class BranchOut(BaseModel):
    id: uuid.UUID
    name: str
    user_count: int
    created_at: datetime


class BranchCreateIn(BaseModel):
    name: str


class BranchUpdateIn(BaseModel):
    name: str


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    branch_id: uuid.UUID | None
    is_active: bool
    created_at: datetime


class AdminUserCreateIn(BaseModel):
    email: str
    role: str
    branch_id: uuid.UUID | None = None
    first_name: str | None = None
    last_name: str | None = None
    # If set, links this existing Keycloak subject instead of provisioning a
    # new Keycloak user -- the documented manual fallback when no Keycloak
    # admin client is configured (see keycloak_admin/client.py).
    keycloak_subject: str | None = None


class AdminUserCreateOut(AdminUserOut):
    invite_email_sent: bool


class AdminUserUpdateIn(BaseModel):
    """All fields optional (PATCH semantics). `branch_id: null` in the request
    body explicitly clears the branch; the endpoint distinguishes that from
    "field omitted" via `model_fields_set`, not this schema's defaults."""

    role: str | None = None
    branch_id: uuid.UUID | None = None
    is_active: bool | None = None


class PermissionMatrixOut(BaseModel):
    roles: list[str]
    permissions: list[str]
    defaults: dict[str, list[str]]
    matrix: dict[str, dict[str, bool]]


class PermissionMatrixIn(BaseModel):
    matrix: dict[str, dict[str, bool]]


class AppAssignmentOut(BaseModel):
    branch_id: uuid.UUID | None
    branch_name: str | None
    is_enabled: bool


class AppOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    is_entitled: bool
    assignments: list[AppAssignmentOut]


class AppAssignmentIn(BaseModel):
    branch_id: uuid.UUID | None = None
    is_enabled: bool


class AvailableAppOut(BaseModel):
    """Lean, non-admin shape: what an ordinary user (any role) can see about an
    app they're entitled to use -- no id/is_entitled/assignments plumbing."""

    key: str
    name: str
    description: str | None
