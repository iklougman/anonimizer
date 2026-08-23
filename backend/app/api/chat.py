from __future__ import annotations

import decimal
import json
import logging
import time
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import iterate_in_threadpool

from app.api.schemas import SendMessageIn
from app.auth.dependencies import get_current_user, get_db_session, require_app_entitlement
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.audit_event_repository import AuditEventRepository
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.llm_request_repository import LLMRequestRepository
from app.db.repositories.message_repository import MessageRepository
from app.db.session import tenant_scoped_session
from app.llm_gateway.provider import (
    ChatMessage,
    LLMProvider,
    LLMProviderError,
    StreamUsage,
    TOKEN_PRESERVATION_SYSTEM_PROMPT,
)
from app.llm_gateway.registry import get_provider
from app.llm_gateway.stream_buffer import SentenceBuffer
from app.privacy_gateway.output_guard.guard import LeakageDetectedError, UnresolvedTokenError
from app.privacy_gateway.pipeline import Pipeline, ResidualPIIError, get_pipeline
from app.privacy_gateway.risk_scoring.scorer import HighRiskMessageError, LowConfidenceSpanError

router = APIRouter(prefix="/api/conversations", tags=["messages"])

logger = logging.getLogger(__name__)

FAIL_CLOSED_MESSAGE = "Sensitive information could not be safely processed."
_TITLE_MAX_LENGTH = 60


def _derive_title(sanitized_prompt: str) -> str:
    stripped = sanitized_prompt.strip()
    if len(stripped) <= _TITLE_MAX_LENGTH:
        return stripped
    return stripped[:_TITLE_MAX_LENGTH].rsplit(" ", 1)[0] + "…"


def _sse_token(delta: str) -> str:
    return f"event: token\ndata: {json.dumps({'delta': delta})}\n\n"


def _sse_done(message_id: uuid.UUID, created_at) -> str:
    return (
        "event: done\n"
        f"data: {json.dumps({'id': str(message_id), 'created_at': created_at.isoformat()})}\n\n"
    )


def _sse_error(detail: str) -> str:
    return f"event: error\ndata: {json.dumps({'detail': detail})}\n\n"


def _stream_and_guard(
    pipeline: Pipeline,
    provider: LLMProvider,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    messages: list[ChatMessage],
    sanitized_prompt: str,
    started_at: float,
) -> Iterator[str]:
    """Real token-by-token streaming with the guard run per completed chunk.

    Each sentence/paragraph-sized chunk is independently run through the
    existing, unmodified `pipeline.deanonymize()` (mask-then-rescan leakage
    check + token resolution) before being released to the client -- real
    streaming without waiting for the whole response, at the cost of a
    slightly weaker cross-sentence leakage check than scanning the full reply
    at once (an accepted trade-off).

    On a mid-stream guard failure, any `event: token` frames already sent were
    each independently guard-approved and stay sent; the turn as a whole is
    never persisted (no assistant Message row), matching today's fail-closed
    contract for the stored record and future conversation history -- only
    what already left the network boundary differs from the pre-streaming
    behavior, which could not partially deliver anything.

    Deliberately does NOT take the request's `Depends(get_db_session)` session
    as a parameter: FastAPI closes yield-based dependencies as soon as the
    endpoint function *returns* the StreamingResponse object, not once the
    streamed body has actually finished sending. A long LLM call (seconds to
    tens of seconds) easily outlives that, so a session borrowed from the
    request dependency would already be closed -- and its RLS tenant-context
    (`app.current_tenant_id`, set per-session in tenant_scoped_session) torn
    down -- by the time this generator tries to persist the assistant message,
    failing every write with "new row violates row-level security policy".
    This generator instead opens its own tenant_scoped_session exactly when it
    needs one, held open for exactly as long as that block needs it,
    independent of the request's own dependency lifecycle.
    """
    buffer = SentenceBuffer()
    sanitized_chunks: list[str] = []
    usage: StreamUsage | None = None

    try:
        for item in provider.stream(messages):
            if isinstance(item, StreamUsage):
                usage = item
                continue
            for completed in buffer.feed(item.text):
                human_readable = pipeline.deanonymize(
                    tenant_id, "conversation", conversation_id, completed
                )
                sanitized_chunks.append(completed)
                yield _sse_token(human_readable)

        remainder = buffer.flush_remainder()
        if remainder is not None:
            human_readable = pipeline.deanonymize(
                tenant_id, "conversation", conversation_id, remainder
            )
            sanitized_chunks.append(remainder)
            yield _sse_token(human_readable)

    except LLMProviderError as exc:
        logger.warning(
            "chat.llm_call FAILED tenant_id=%s conversation_id=%s provider=%s model=%s: %s",
            tenant_id, conversation_id, provider.name, provider.model, exc,
        )
        yield _sse_error("the language model provider is unavailable")
        return
    except (LeakageDetectedError, UnresolvedTokenError) as exc:
        # entity_type/token record *what* was caught (entity categories for a
        # leak, the fabricated token strings for an unresolved one) so the audit
        # trail is queryable without re-parsing the exception message. Never the
        # leaked PII text itself -- only its category, which isn't sensitive.
        if isinstance(exc, LeakageDetectedError):
            entity_type = ", ".join(dict.fromkeys(exc.entity_types)) or "UNKNOWN"
            token = ""
        else:
            entity_type = "UNKNOWN"
            token = ", ".join(exc.tokens)
        with tenant_scoped_session(tenant_id) as session:
            AuditEventRepository(session).create(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                event_type=type(exc).__name__,
                entity_type=entity_type,
                token=token,
                actor=str(user_id),
            )
        logger.info(
            "chat.audit_event tenant_id=%s conversation_id=%s event_type=%s entity_type=%s recorded",
            tenant_id, conversation_id, type(exc).__name__, entity_type,
        )
        yield _sse_error(FAIL_CLOSED_MESSAGE)
        return

    # sanitized_content is the concatenated token-shaped chunks: already
    # token-shaped (the LLM only ever saw pseudonymized input), never the
    # human-readable reconstruction -- messages store only pseudonymized text
    # (master design doc §4). Same shape as the pre-streaming single-call
    # `completion.text` -- only how it's assembled changes.
    full_sanitized_text = "".join(sanitized_chunks)
    latency_ms = int((time.monotonic() - started_at) * 1000)
    tokens_in = usage.tokens_in if usage else 0
    tokens_out = usage.tokens_out if usage else 0
    cost_usd = usage.cost_usd if usage else decimal.Decimal(0)

    logger.info(
        "chat.llm_response tenant_id=%s conversation_id=%s provider=%s model=%s "
        "latency_ms=%d tokens_in=%d tokens_out=%d",
        tenant_id, conversation_id, provider.name, provider.model, latency_ms, tokens_in, tokens_out,
    )

    with tenant_scoped_session(tenant_id) as session:
        assistant_message = MessageRepository(session).create(
            tenant_id, conversation_id, role="assistant", sanitized_content=full_sanitized_text
        )
        LLMRequestRepository(session).create(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            provider=provider.name,
            model=provider.model,
            sanitized_prompt=sanitized_prompt,
            sanitized_response=full_sanitized_text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        ConversationRepository(session).touch(tenant_id, conversation_id)

    logger.info(
        "chat.complete tenant_id=%s conversation_id=%s message_id=%s -> PASS",
        tenant_id, conversation_id, assistant_message.id,
    )
    yield _sse_done(assistant_message.id, assistant_message.created_at)


@router.post("/{conversation_id}/messages")
def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageIn,
    user: AuthenticatedUser = Depends(get_current_user),
    _entitlement: AuthenticatedUser = Depends(require_app_entitlement(app_key="anonymization")),
    session: Session = Depends(get_db_session),
    pipeline: Pipeline = Depends(get_pipeline),
    provider: LLMProvider = Depends(get_provider),
) -> StreamingResponse:
    conversation_repo = ConversationRepository(session)
    conversation = conversation_repo.get(user.tenant_id, conversation_id)
    if conversation is None or conversation.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="conversation not found")

    logger.info(
        "chat.receive tenant_id=%s conversation_id=%s user_id=%s length=%d",
        user.tenant_id, conversation_id, user.user_id, len(body.content),
    )

    try:
        sanitized_prompt = pipeline.sanitize(
            user.tenant_id, "conversation", conversation_id, body.content
        )
    except (LowConfidenceSpanError, HighRiskMessageError, ResidualPIIError) as exc:
        raise HTTPException(status_code=422, detail=FAIL_CLOSED_MESSAGE) from exc

    # Build the full message list: the token-preservation system prompt, the
    # sanitized prior turns (already-stored sanitized_content), and the new
    # sanitized prompt as the final user message. Only sanitized text ever
    # reaches the provider (ADR-0013) -- history replays the same pseudonymized
    # text that was approved to leave the boundary on its original turn.
    #
    # History is loaded *before* the new user message is persisted so the new
    # turn is not double-counted: it is appended explicitly as the final user
    # message below.
    message_repo = MessageRepository(session)
    prior_messages = message_repo.list_for_conversation(user.tenant_id, conversation_id)
    messages = [ChatMessage(role="system", content=TOKEN_PRESERVATION_SYSTEM_PROMPT)]
    for prior in prior_messages:
        messages.append(ChatMessage(role=prior.role, content=prior.sanitized_content))
    messages.append(ChatMessage(role="user", content=sanitized_prompt))

    # Persist and commit the user's message before calling the LLM provider, so a
    # provider outage doesn't roll back and lose an already-sanitized message.
    message_repo.create(
        user.tenant_id, conversation_id, role="user", sanitized_content=sanitized_prompt
    )
    if conversation.title is None:
        conversation_repo.set_title(user.tenant_id, conversation_id, _derive_title(sanitized_prompt))
    conversation_repo.touch(user.tenant_id, conversation_id)
    session.commit()

    logger.info(
        "chat.llm_call tenant_id=%s conversation_id=%s provider=%s model=%s history_turns=%d",
        user.tenant_id, conversation_id, provider.name, provider.model, len(prior_messages),
    )
    started_at = time.monotonic()

    # iterate_in_threadpool is load-bearing, not cosmetic: _stream_and_guard does
    # blocking httpx I/O (the provider call) and blocking SQLAlchemy commits. If
    # Starlette iterated this generator directly on the asyncio event-loop
    # thread, one slow/streaming request would stall every other concurrent
    # request on this uvicorn worker -- invisible today only because the old
    # fake-replay generator never did any I/O.
    return StreamingResponse(
        iterate_in_threadpool(
            _stream_and_guard(
                pipeline,
                provider,
                user.tenant_id,
                conversation_id,
                user.user_id,
                messages,
                sanitized_prompt,
                started_at,
            )
        ),
        media_type="text/event-stream",
    )
