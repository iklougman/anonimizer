from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.schemas import SendMessageIn
from app.auth.dependencies import get_current_user, get_db_session
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.audit_event_repository import AuditEventRepository
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.llm_request_repository import LLMRequestRepository
from app.db.repositories.message_repository import MessageRepository
from app.llm_gateway.provider import LLMProvider, LLMProviderError
from app.llm_gateway.registry import get_provider
from app.privacy_gateway.output_guard.guard import LeakageDetectedError, UnresolvedTokenError
from app.privacy_gateway.pipeline import Pipeline, get_pipeline
from app.privacy_gateway.risk_scoring.scorer import HighRiskMessageError, LowConfidenceSpanError

router = APIRouter(prefix="/api/conversations", tags=["messages"])

FAIL_CLOSED_MESSAGE = "Sensitive information could not be safely processed."
_CHUNK_WORDS = 3
_TITLE_MAX_LENGTH = 60


def _derive_title(sanitized_prompt: str) -> str:
    stripped = sanitized_prompt.strip()
    if len(stripped) <= _TITLE_MAX_LENGTH:
        return stripped
    return stripped[:_TITLE_MAX_LENGTH].rsplit(" ", 1)[0] + "…"


def _replay_as_sse(text: str, message_id: uuid.UUID, created_at) -> Iterator[str]:
    words = text.split(" ")
    for i in range(0, len(words), _CHUNK_WORDS):
        chunk = " ".join(words[i : i + _CHUNK_WORDS])
        if i + _CHUNK_WORDS < len(words):
            chunk += " "
        yield f"event: token\ndata: {json.dumps({'delta': chunk})}\n\n"
    yield (
        "event: done\n"
        f"data: {json.dumps({'id': str(message_id), 'created_at': created_at.isoformat()})}\n\n"
    )


@router.post("/{conversation_id}/messages")
def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageIn,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    pipeline: Pipeline = Depends(get_pipeline),
    provider: LLMProvider = Depends(get_provider),
) -> StreamingResponse:
    conversation_repo = ConversationRepository(session)
    conversation = conversation_repo.get(user.tenant_id, conversation_id)
    if conversation is None or conversation.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="conversation not found")

    try:
        sanitized_prompt = pipeline.sanitize(user.tenant_id, conversation_id, body.content)
    except (LowConfidenceSpanError, HighRiskMessageError) as exc:
        raise HTTPException(status_code=422, detail=FAIL_CLOSED_MESSAGE) from exc

    # Persist and commit the user's message before calling the LLM provider, so a
    # provider outage doesn't roll back and lose an already-sanitized message.
    message_repo = MessageRepository(session)
    message_repo.create(
        user.tenant_id, conversation_id, role="user", sanitized_content=sanitized_prompt
    )
    if conversation.title is None:
        conversation_repo.set_title(user.tenant_id, conversation_id, _derive_title(sanitized_prompt))
    conversation_repo.touch(user.tenant_id, conversation_id)
    session.commit()

    started_at = time.monotonic()
    try:
        completion = provider.complete(sanitized_prompt)
    except LLMProviderError as exc:
        raise HTTPException(
            status_code=502, detail="the language model provider is unavailable"
        ) from exc
    latency_ms = int((time.monotonic() - started_at) * 1000)

    try:
        human_readable = pipeline.deanonymize(user.tenant_id, conversation_id, completion.text)
    except (LeakageDetectedError, UnresolvedTokenError) as exc:
        AuditEventRepository(session).create(
            tenant_id=user.tenant_id,
            conversation_id=conversation_id,
            event_type=type(exc).__name__,
            entity_type="UNKNOWN",
            token="",
            actor=str(user.user_id),
        )
        session.commit()
        raise HTTPException(
            status_code=500, detail="the response could not be safely returned"
        ) from exc

    # sanitized_content is the raw completion text: already token-shaped (the LLM
    # only ever saw pseudonymized input), never the human-readable reconstruction --
    # messages store only pseudonymized text (master design doc §4).
    assistant_message = message_repo.create(
        user.tenant_id, conversation_id, role="assistant", sanitized_content=completion.text
    )
    LLMRequestRepository(session).create(
        tenant_id=user.tenant_id,
        conversation_id=conversation_id,
        provider=provider.name,
        model=provider.model,
        sanitized_prompt=sanitized_prompt,
        sanitized_response=completion.text,
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        cost_usd=completion.cost_usd,
        latency_ms=latency_ms,
    )
    conversation_repo.touch(user.tenant_id, conversation_id)
    session.commit()

    return StreamingResponse(
        _replay_as_sse(human_readable, assistant_message.id, assistant_message.created_at),
        media_type="text/event-stream",
    )
