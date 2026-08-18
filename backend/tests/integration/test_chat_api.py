import decimal
import json
import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.permissions import DEFAULT_PERMISSIONS
from app.auth.tenant_resolver import AuthenticatedUser
from app.config import get_settings
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.llm_gateway.provider import LLMCompletion, LLMProviderError
from app.llm_gateway.registry import get_provider
from app.main import app
from app.models import LLMRequest, Message
from app.privacy_gateway.detectors.base import normalize
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import OutputGuard
from app.privacy_gateway.pipeline import Pipeline, get_pipeline
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.risk_scoring.scorer import RiskScorer
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault


class _StubProvider:
    name = "stub"
    model = "stub-model"

    def __init__(self, response_text: str | None = None, error: Exception | None = None):
        self._response_text = response_text
        self._error = error

    def complete(self, prompt: str) -> LLMCompletion:
        if self._error is not None:
            raise self._error
        return LLMCompletion(
            text=self._response_text, tokens_in=10, tokens_out=10, cost_usd=decimal.Decimal("0")
        )


@pytest.fixture
def scope():
    # Must use the same master key as the process-wide get_pipeline()'s TokenVault
    # (Settings.master_key_path, set up once in tests/conftest.py) -- a tenant's DEK
    # is wrapped with this key at creation, and get_pipeline()'s vault (used by the
    # send-message route by default) unwraps it with the same key at deanonymize()
    # time. A private per-test tmp_path master key would raise InvalidUnwrap.
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
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
        user_id = user.id
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id
    return tenant_id, user_id, conversation_id


@pytest.fixture
def authenticated(scope):
    tenant_id, user_id, _ = scope
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id,
        user_id=user_id,
        role="doctor",
        branch_id=None,
        email="doc@example.com",
        permissions=DEFAULT_PERMISSIONS["doctor"],
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _use_provider(provider) -> None:
    app.dependency_overrides[get_provider] = lambda: provider


def _clear_provider_override() -> None:
    app.dependency_overrides.pop(get_provider, None)


def test_send_message_streams_the_validated_response(scope, authenticated):
    tenant_id, user_id, conversation_id = scope
    _use_provider(_StubProvider(response_text="Das klingt nach einer guten Genesung."))
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Wie geht es dem Patienten?"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [line for line in response.text.split("\n\n") if line.strip()]
    deltas = "".join(
        json.loads(line.split("data: ", 1)[1])["delta"]
        for line in events
        if line.startswith("event: token")
    )
    assert deltas == "Das klingt nach einer guten Genesung."
    assert any(line.startswith("event: done") for line in events)

    with tenant_scoped_session(tenant_id) as session:
        messages = session.execute(
            sa.select(Message).where(Message.conversation_id == conversation_id)
        ).scalars().all()
        assert {m.role for m in messages} == {"user", "assistant"}

        llm_requests = session.execute(
            sa.select(LLMRequest).where(LLMRequest.conversation_id == conversation_id)
        ).scalars().all()
        assert len(llm_requests) == 1
        assert llm_requests[0].provider == "stub"

    _clear_provider_override()


def test_high_risk_message_is_rejected_with_422(scope, authenticated, tmp_path):
    _, _, conversation_id = scope
    # A standalone Pipeline (same construction as get_pipeline(), see pipeline.py),
    # with an injected rare-disease set -- avoids depending on the real reference
    # data containing "Marfan-Syndrom", and proves HighRiskMessageError propagates
    # through the route as a 422 without ever calling the LLM provider.
    (tmp_path / "high-risk-master.key").write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(tmp_path / "high-risk-master.key"))
    vault = TokenVault(key_provider)
    detector_stack = DetectorStack(RegexDetector(), PresidioDetector(), CustomRecognizers(frozenset()))
    test_pipeline = Pipeline(
        detector_stack=detector_stack,
        risk_scorer=RiskScorer(frozenset({normalize("Marfan-Syndrom")})),
        pseudonymizer=Pseudonymizer(vault),
        output_guard=OutputGuard(detector_stack, vault),
    )
    app.dependency_overrides[get_pipeline] = lambda: test_pipeline
    _use_provider(_StubProvider(response_text="unused"))
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": (
                "Der Patient ist 67 Jahre alt, wurde in Heidelberg behandelt und am "
                "12.03.2024 mit Marfan-Syndrom diagnostiziert."
            )
        },
    )

    assert response.status_code == 422

    app.dependency_overrides.pop(get_pipeline, None)
    _clear_provider_override()


def test_provider_failure_is_a_502_and_the_user_message_survives(scope, authenticated):
    tenant_id, _, conversation_id = scope
    _use_provider(_StubProvider(error=LLMProviderError("provider is down")))
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Hallo."}
    )

    assert response.status_code == 502

    with tenant_scoped_session(tenant_id) as session:
        messages = session.execute(
            sa.select(Message).where(Message.conversation_id == conversation_id)
        ).scalars().all()
        assert len(messages) == 1
        assert messages[0].role == "user"

    _clear_provider_override()


def test_leaked_response_is_a_500_and_is_audit_logged(scope, authenticated):
    tenant_id, _, conversation_id = scope
    _use_provider(_StubProvider(response_text="Der Patient heißt Anna Schmitt."))
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Hallo."}
    )

    assert response.status_code == 500

    from app.models import AuditEvent

    with tenant_scoped_session(tenant_id) as session:
        events = session.execute(
            sa.select(AuditEvent).where(AuditEvent.conversation_id == conversation_id)
        ).scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "LeakageDetectedError"

    _clear_provider_override()


def test_send_message_to_unknown_conversation_is_404(authenticated):
    client = TestClient(app)
    response = client.post(
        f"/api/conversations/{uuid.uuid4()}/messages", json={"content": "Hallo."}
    )
    assert response.status_code == 404
