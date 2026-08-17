import decimal
import uuid

import sqlalchemy as sa

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.llm_request_repository import LLMRequestRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import LLMRequest
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def test_create_persists_an_llm_request_row(tmp_path):
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
        request = LLMRequestRepository(session).create(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            provider="ollama",
            model="llama3.1",
            sanitized_prompt="Hallo PATIENT_1234567890",
            sanitized_response="Guten Tag PATIENT_1234567890",
            tokens_in=12,
            tokens_out=8,
            cost_usd=decimal.Decimal("0"),
            latency_ms=245,
        )
        request_id = request.id

    with tenant_scoped_session(tenant_id) as session:
        stored = session.execute(
            sa.select(LLMRequest).where(LLMRequest.id == request_id)
        ).scalar_one()
        assert stored.provider == "ollama"
        assert stored.tokens_in == 12
        assert stored.cost_usd == decimal.Decimal("0")
