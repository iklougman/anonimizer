import uuid

import sqlalchemy as sa

from app.db.repositories.audit_event_repository import AuditEventRepository
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import AuditEvent
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def test_create_persists_an_audit_event_row(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        conversation_id = ConversationRepository(session).create(tenant_id, user.id).id
        event = AuditEventRepository(session).create(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            event_type="LeakageDetectedError",
            entity_type="UNKNOWN",
            token="",
            actor=str(user.id),
        )
        event_id = event.id

    with tenant_scoped_session(tenant_id) as session:
        stored = session.execute(
            sa.select(AuditEvent).where(AuditEvent.id == event_id)
        ).scalar_one()
        assert stored.event_type == "LeakageDetectedError"
        assert stored.conversation_id == conversation_id
