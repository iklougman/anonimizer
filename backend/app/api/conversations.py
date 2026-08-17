from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.schemas import ConversationSummary, MessageOut
from app.auth.dependencies import get_current_user, get_db_session
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.message_repository import MessageRepository
from app.privacy_gateway.pipeline import Pipeline, get_pipeline

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _to_summary(conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[ConversationSummary]:
    conversations = ConversationRepository(session).list_for_user(user.tenant_id, user.user_id)
    return [_to_summary(c) for c in conversations]


@router.post("", response_model=ConversationSummary, status_code=201)
def create_conversation(
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ConversationSummary:
    conversation = ConversationRepository(session).create(user.tenant_id, user.user_id)
    return _to_summary(conversation)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(
    conversation_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    pipeline: Pipeline = Depends(get_pipeline),
) -> list[MessageOut]:
    conversation = ConversationRepository(session).get(user.tenant_id, conversation_id)
    if conversation is None or conversation.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="conversation not found")

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
    conversation = repo.get(user.tenant_id, conversation_id)
    if conversation is None or conversation.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="conversation not found")
    repo.soft_delete(user.tenant_id, conversation_id)
    return Response(status_code=204)
