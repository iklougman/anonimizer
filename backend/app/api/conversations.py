from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.schemas import ConversationDetail, ConversationSummary, MessageOut
from app.auth.dependencies import get_current_user, get_db_session, require_permission
from app.auth.permissions import Permission, can_read_conversation, visibility_scope
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.audit_event_repository import AuditEventRepository
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.message_repository import MessageRepository
from app.models import Conversation
from app.privacy_gateway.pipeline import Pipeline, get_pipeline

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _to_summary(
    conversation: Conversation, owner_user_id: uuid.UUID, owner_email: str, viewer: AuthenticatedUser
) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        is_own=owner_user_id == viewer.user_id,
    )


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[ConversationSummary]:
    scope = visibility_scope(user)
    rows = ConversationRepository(session).list_visible(
        user.tenant_id, user.user_id, user.branch_id, scope
    )
    return [_to_summary(conversation, owner.id, owner.email, user) for conversation, owner in rows]


@router.post("", response_model=ConversationSummary, status_code=201)
def create_conversation(
    user: AuthenticatedUser = Depends(require_permission(Permission.CONVERSATIONS_CREATE)),
    session: Session = Depends(get_db_session),
) -> ConversationSummary:
    conversation = ConversationRepository(session).create(user.tenant_id, user.user_id)
    # The creator is always the owner -- no join needed to build the summary.
    return _to_summary(conversation, user.user_id, user.email, user)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ConversationDetail:
    found = ConversationRepository(session).get_with_owner(user.tenant_id, conversation_id)
    if found is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    conversation, owner = found
    if not can_read_conversation(user, owner):
        # 404, not 403: a permission-denied response would confirm the
        # conversation exists to a user who cannot see it.
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationDetail(**_to_summary(conversation, owner.id, owner.email, user).model_dump())


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(
    conversation_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    pipeline: Pipeline = Depends(get_pipeline),
) -> list[MessageOut]:
    found = ConversationRepository(session).get_with_owner(user.tenant_id, conversation_id)
    if found is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    conversation, owner = found
    if not can_read_conversation(user, owner):
        raise HTTPException(status_code=404, detail="conversation not found")

    if owner.id != user.user_id:
        AuditEventRepository(session).create(
            tenant_id=user.tenant_id,
            conversation_id=conversation_id,
            event_type="SharedConversationRead",
            entity_type="CONVERSATION",
            token="",
            actor=str(user.user_id),
        )

    messages = MessageRepository(session).list_for_conversation(user.tenant_id, conversation_id)
    return [
        MessageOut(
            id=message.id,
            role=message.role,
            content=pipeline.deanonymize(user.tenant_id, conversation_id, message.sanitized_content),
            created_at=message.created_at,
        )
        for message in messages
    ]


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    repo = ConversationRepository(session)
    found = repo.get_with_owner(user.tenant_id, conversation_id)
    if found is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    _, owner = found
    is_owner = owner.id == user.user_id
    if not is_owner and Permission.CONVERSATIONS_DELETE_ANY not in user.permissions:
        raise HTTPException(status_code=404, detail="conversation not found")
    repo.soft_delete(user.tenant_id, conversation_id)
    return Response(status_code=204)
