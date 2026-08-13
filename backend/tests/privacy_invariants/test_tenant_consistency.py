"""Composite foreign keys enforce that a child row's tenant matches its parent's.

Postgres runs referential-integrity checks with Row-Level Security bypassed
(documented behavior — the RI triggers execute as the table owner with RLS off). So
a single-column `messages.conversation_id -> conversations.id` FK could not see the
tenant dimension at all: writing a message with `tenant_id = A` pointing at a
conversation owned by tenant B satisfied both the `WITH CHECK` policy on `messages`
(which only validates `messages.tenant_id`) and the FK check. Reads still filtered by
`tenant_id`, so this was never a confidentiality leak, but the schema's central
invariant had no database-level enforcement.

Migration 0004 replaces those with composite `(tenant_id, <parent_id>)` FKs. These
tests prove the mismatched write is now rejected, and that legitimately-scoped writes
still succeed.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Message, Tenant


def _create_tenant(name: str) -> uuid.UUID:
    with SessionLocal() as session:
        tenant = Tenant(name=name, keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30)
        session.add(tenant)
        session.commit()
        return tenant.id


def _create_conversation(tenant_id: uuid.UUID) -> uuid.UUID:
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id,
            keycloak_subject=f"subject-{uuid.uuid4()}",
            email="doc@example.com",
            role="doctor",
        )
        return ConversationRepository(session).create(tenant_id, user.id).id


def test_message_cannot_reference_another_tenants_conversation():
    tenant_a = _create_tenant("Consistency Tenant A")
    tenant_b = _create_tenant("Consistency Tenant B")
    conversation_b = _create_conversation(tenant_b)

    # tenant_id = A satisfies the WITH CHECK policy on `messages`; only the composite
    # FK can catch that conversation_b belongs to tenant B.
    with pytest.raises(IntegrityError):
        with tenant_scoped_session(tenant_a) as session:
            session.add(
                Message(
                    tenant_id=tenant_a,
                    conversation_id=conversation_b,
                    role="user",
                    sanitized_content="cross-tenant message",
                )
            )


def test_message_with_matching_tenant_is_accepted():
    """Guard against the composite FK being so strict it breaks legitimate writes."""
    tenant_a = _create_tenant("Consistency Tenant C")
    conversation_a = _create_conversation(tenant_a)

    with tenant_scoped_session(tenant_a) as session:
        session.add(
            Message(
                tenant_id=tenant_a,
                conversation_id=conversation_a,
                role="user",
                sanitized_content="in-tenant message",
            )
        )
