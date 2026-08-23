import json
import uuid
from pathlib import Path

import pytest

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.detectors.base import normalize
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import OutputGuard
from app.privacy_gateway.pipeline import Pipeline
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.risk_scoring.scorer import RiskScorer
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault
from tests.conftest import grant_app_entitlement

CORPUS_DIR = Path(__file__).resolve().parents[3] / "evaluation" / "golden_corpus"


def load_golden_corpus() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(CORPUS_DIR.glob("*.json"))
    ]


@pytest.fixture(scope="session")
def golden_corpus() -> list[dict]:
    return load_golden_corpus()


@pytest.fixture(scope="session")
def corpus_key_provider(tmp_path_factory):
    master_key_path = tmp_path_factory.mktemp("keys") / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


@pytest.fixture(scope="session")
def corpus_vault(corpus_key_provider) -> TokenVault:
    return TokenVault(corpus_key_provider)


@pytest.fixture(scope="session")
def corpus_pipeline(golden_corpus, corpus_vault) -> Pipeline:
    """A pipeline whose reference data is derived from the corpus itself.

    The real ORDO OWL and Krankenhausverzeichnis are 50 MB of build-time input and
    minutes of parse time (design spec §6); injecting the corpus's own rare-disease
    names and hospital names keeps this suite hermetic and fast while exercising
    exactly the same code paths. `tests/unit/test_reference_data.py` covers the real
    files.
    """
    rare_diseases = frozenset(
        normalize(name) for note in golden_corpus for name in note["rare_diseases"]
    )
    hospitals = frozenset(
        normalize(entity["text"])
        for note in golden_corpus
        for entity in note["entities"]
        if entity["entity_type"] == "HOSPITAL"
    )
    stack = DetectorStack(RegexDetector(), PresidioDetector(), CustomRecognizers(hospitals))
    return Pipeline(
        detector_stack=stack,
        risk_scorer=RiskScorer(rare_diseases),
        pseudonymizer=Pseudonymizer(corpus_vault),
        output_guard=OutputGuard(stack, corpus_vault),
    )


def new_scope(key_provider) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a fresh tenant + user + conversation and return (tenant_id, conversation_id)."""
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    grant_app_entitlement(tenant_id)
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id,
            keycloak_subject=f"sub-{uuid.uuid4()}",
            email="doc@example.com",
            role="doctor",
        )
        conversation_id = ConversationRepository(session).create(tenant_id, user.id).id
    return tenant_id, conversation_id


@pytest.fixture
def corpus_scope(corpus_key_provider) -> tuple[uuid.UUID, uuid.UUID]:
    return new_scope(corpus_key_provider)


def new_scope_with_user(key_provider) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Like new_scope(), but also returns the user id -- needed by the chat-API
    corpus test to build an AuthenticatedUser for dependency_overrides."""
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    grant_app_entitlement(tenant_id)
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}", email="doc@example.com", role="doctor"
        )
        user_id = user.id
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id
    return tenant_id, user_id, conversation_id
