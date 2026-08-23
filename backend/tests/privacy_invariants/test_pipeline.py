import re
import uuid

import pytest

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import OutputGuard
from app.privacy_gateway.pipeline import (
    HighRiskMessageError,
    LeakageDetectedError,
    LowConfidenceSpanError,
    Pipeline,
    ResidualPIIError,
    UnresolvedTokenError,
)
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.risk_scoring.scorer import RiskScorer
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault

DISEASES = frozenset({"zystische fibrose"})
HOSPITALS = frozenset({"charité universitätsmedizin berlin"})


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


@pytest.fixture
def scope(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}",
            email="doc@example.com", role="doctor",
        )
        conversation_id = ConversationRepository(session).create(tenant_id, user.id).id
    return tenant_id, conversation_id


@pytest.fixture(scope="module")
def detector_stack():
    return DetectorStack(RegexDetector(), PresidioDetector(), CustomRecognizers(HOSPITALS))


class _StubDetectorStack:
    """A minimal stand-in for DetectorStack that returns a fixed span list,
    used to put a below-threshold-confidence span in front of the risk scorer
    without depending on Presidio actually producing one (design spec §3;
    see test_risk_scorer.py's own `_span(..., confidence=...)` pattern)."""

    def __init__(self, spans):
        self._spans = spans

    def detect(self, text):
        return self._spans


@pytest.fixture
def pipeline(detector_stack, key_provider):
    vault = TokenVault(key_provider)
    return Pipeline(
        detector_stack=detector_stack,
        risk_scorer=RiskScorer(DISEASES),
        pseudonymizer=Pseudonymizer(vault),
        output_guard=OutputGuard(detector_stack, vault),
    )


def test_sanitize_removes_every_identifier(scope, pipeline):
    tenant_id, conversation_id = scope
    text = (
        "Patient Lukas Berger, Versichertennummer A123456789, wurde am 12.03.2024 "
        "im Universitätsklinikum Heidelberg von Dr. Anna Schmitt aufgenommen."
    )

    sanitized = pipeline.sanitize(tenant_id, "conversation", conversation_id, text)

    for raw in ("Lukas Berger", "A123456789", "12.03.2024",
                "Universitätsklinikum Heidelberg", "Anna Schmitt"):
        assert raw not in sanitized


def test_sanitize_keeps_the_non_identifying_text(scope, pipeline):
    tenant_id, conversation_id = scope
    sanitized = pipeline.sanitize(
        tenant_id, "conversation", conversation_id, "Patient Lukas Berger wurde aufgenommen."
    )
    assert sanitized.startswith("Patient ")
    assert sanitized.endswith(" wurde aufgenommen.")


def test_sanitize_raises_on_the_high_risk_combination(scope, pipeline):
    """Design spec §3: age + city + date + rare disease rejects the whole message."""
    tenant_id, conversation_id = scope
    text = (
        "Der 19-jährige Patient Simon Kraus aus Tübingen wurde am 11.05.2024 "
        "mit der Diagnose Zystische Fibrose vorgestellt."
    )
    with pytest.raises(HighRiskMessageError):
        pipeline.sanitize(tenant_id, "conversation", conversation_id, text)


def test_low_confidence_span_propagates_out_of_sanitize(scope, key_provider):
    """Design spec §3: a span below the confidence threshold blocks the whole
    message, same fail-closed contract as HighRiskMessageError. sanitize() must
    not catch or wrap it."""
    tenant_id, conversation_id = scope
    text = "Patient Lukas Berger wurde aufgenommen."
    low_confidence_span = Span(8, 20, "PERSON", 0.2, "test")
    vault = TokenVault(key_provider)
    stub_detector_stack = _StubDetectorStack([low_confidence_span])
    pipeline_with_stub_detector = Pipeline(
        detector_stack=stub_detector_stack,
        risk_scorer=RiskScorer(DISEASES),
        pseudonymizer=Pseudonymizer(vault),
        output_guard=OutputGuard(stub_detector_stack, vault),
    )

    with pytest.raises(LowConfidenceSpanError):
        pipeline_with_stub_detector.sanitize(tenant_id, "conversation", conversation_id, text)


def test_a_rejected_message_writes_no_partially_sanitized_output(scope, pipeline):
    tenant_id, conversation_id = scope
    text = (
        "Der 19-jährige Patient Simon Kraus aus Tübingen wurde am 11.05.2024 "
        "mit der Diagnose Zystische Fibrose vorgestellt."
    )
    with pytest.raises(HighRiskMessageError) as excinfo:
        pipeline.sanitize(tenant_id, "conversation", conversation_id, text)
    assert "Simon Kraus" not in str(excinfo.value)


def test_round_trip_restores_the_original_values(scope, pipeline):
    tenant_id, conversation_id = scope
    text = "Patient Lukas Berger wurde am 12.03.2024 aufgenommen."

    sanitized = pipeline.sanitize(tenant_id, "conversation", conversation_id, text)
    restored = pipeline.deanonymize(tenant_id, "conversation", conversation_id, sanitized)

    assert restored == text


def test_deanonymize_rejects_leaked_pii(scope, pipeline):
    tenant_id, conversation_id = scope
    with pytest.raises(LeakageDetectedError):
        pipeline.deanonymize(
            tenant_id, "conversation", conversation_id, "Die Versichertennummer lautet A123456789."
        )


def test_deanonymize_rejects_a_fabricated_token(scope, pipeline):
    tenant_id, conversation_id = scope
    with pytest.raises(UnresolvedTokenError):
        pipeline.deanonymize(tenant_id, "conversation", conversation_id, "Siehe PATIENT_0000000000.")


class _NoOpPseudonymizer:
    """Stands in for a Pseudonymizer.apply() that failed to substitute anything --
    simulates the exact bug assert_no_raw_pii exists to catch, so sanitize() must
    fail closed on its own output rather than forward it to an LLM."""

    def apply(self, tenant_id, scope_type, scope_id, text, spans):
        return text


def test_sanitize_fails_closed_if_pseudonymization_leaves_pii_behind(
    scope, detector_stack, key_provider
):
    tenant_id, conversation_id = scope
    vault = TokenVault(key_provider)
    broken_pipeline = Pipeline(
        detector_stack=detector_stack,
        risk_scorer=RiskScorer(DISEASES),
        pseudonymizer=_NoOpPseudonymizer(),
        output_guard=OutputGuard(detector_stack, vault),
    )

    with pytest.raises(ResidualPIIError):
        broken_pipeline.sanitize(
            tenant_id, "conversation", conversation_id, "Patient Lukas Berger wurde aufgenommen."
        )


def test_sanitized_output_contains_only_well_formed_tokens(scope, pipeline):
    tenant_id, conversation_id = scope
    sanitized = pipeline.sanitize(
        tenant_id, "conversation", conversation_id, "Lukas Berger, A123456789, am 12.03.2024."
    )
    tokens = re.findall(r"[A-Z][A-Z_]*_[0-9A-F]{10}", sanitized)
    assert len(tokens) == 3
    assert {token.rsplit("_", 1)[0] for token in tokens} == {
        "PATIENT", "INSURANCE_NUMBER", "DATE"
    }
