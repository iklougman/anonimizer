import re
import uuid

import pytest

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault

TOKEN_SHAPE = re.compile(r"^[A-Z][A-Z_]*_[0-9A-F]{10}$")


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
            tenant_id, keycloak_subject="sub", email="doc@example.com", role="doctor"
        )
        conversation = ConversationRepository(session).create(tenant_id, user.id)
        conversation_id = conversation.id
    return tenant_id, conversation_id


@pytest.fixture
def pseudonymizer(key_provider):
    return Pseudonymizer(TokenVault(key_provider))


def test_replaces_a_span_with_a_vault_token(scope, pseudonymizer):
    tenant_id, conversation_id = scope
    text = "Patient Lukas Berger wurde aufgenommen."
    spans = [Span(8, 20, "PATIENT", 0.85, "custom")]

    result = pseudonymizer.apply(tenant_id, "conversation", conversation_id, text, spans)

    assert "Lukas Berger" not in result
    assert result.startswith("Patient PATIENT_")
    assert result.endswith(" wurde aufgenommen.")


def test_multiple_spans_keep_their_offsets_correct(scope, pseudonymizer):
    """Descending start order (design spec §4) so an earlier replacement never
    shifts the offsets of a span that has not been processed yet."""
    tenant_id, conversation_id = scope
    text = "Lukas Berger, A123456789, am 12.03.2024."
    spans = [
        Span(0, 12, "PATIENT", 0.85, "custom"),
        Span(14, 24, "INSURANCE_NUMBER", 1.0, "regex"),
        Span(29, 39, "DATE", 1.0, "regex"),
    ]

    result = pseudonymizer.apply(tenant_id, "conversation", conversation_id, text, spans)

    assert "Lukas Berger" not in result
    assert "A123456789" not in result
    assert "12.03.2024" not in result
    assert result.count(", ") == 2
    assert result.endswith(".")


def test_spans_supplied_out_of_order_are_still_replaced_correctly(scope, pseudonymizer):
    tenant_id, conversation_id = scope
    text = "Lukas Berger, A123456789, am 12.03.2024."
    spans = [
        Span(29, 39, "DATE", 1.0, "regex"),
        Span(0, 12, "PATIENT", 0.85, "custom"),
        Span(14, 24, "INSURANCE_NUMBER", 1.0, "regex"),
    ]

    result = pseudonymizer.apply(tenant_id, "conversation", conversation_id, text, spans)

    assert "Lukas Berger" not in result
    assert "A123456789" not in result
    assert "12.03.2024" not in result


def test_the_same_value_gets_the_same_token_within_one_call(scope, pseudonymizer):
    """The MVP's exact-string-match determinism, scoped to a single message: the
    vault mints a fresh random token per create_mapping call, so identical values
    are de-duplicated here rather than in the vault."""
    tenant_id, conversation_id = scope
    text = "Lea Sommer kam. Begleitet wurde Lea Sommer von Ingrid Sommer."
    spans = [
        Span(0, 10, "PATIENT", 0.85, "custom"),
        Span(32, 42, "PATIENT", 0.85, "custom"),
        Span(46, 59, "PERSON", 0.85, "custom"),
    ]

    result = pseudonymizer.apply(tenant_id, "conversation", conversation_id, text, spans)
    tokens = re.findall(r"[A-Z][A-Z_]*_[0-9A-F]{10}", result)

    assert len(tokens) == 3
    assert tokens[0] == tokens[1]
    assert tokens[2] != tokens[0]


def test_different_surfaces_of_the_same_person_get_different_tokens(scope, pseudonymizer):
    """The documented MVP coreference limitation, made visible."""
    tenant_id, conversation_id = scope
    text = "Hans Müller kam. Später berichtete Herr Müller über Schmerzen."
    spans = [
        Span(0, 11, "PATIENT", 0.85, "custom"),
        Span(34, 45, "PERSON", 0.85, "custom"),
    ]

    result = pseudonymizer.apply(tenant_id, "conversation", conversation_id, text, spans)
    tokens = re.findall(r"[A-Z][A-Z_]*_[0-9A-F]{10}", result)

    assert len(set(tokens)) == 2


def test_tokens_match_the_adr_0009_shape(scope, pseudonymizer):
    tenant_id, conversation_id = scope
    result = pseudonymizer.apply(
        tenant_id, "conversation", conversation_id, "Lukas Berger", [Span(0, 12, "PATIENT", 0.85, "custom")]
    )
    assert TOKEN_SHAPE.match(result)


def test_issued_tokens_resolve_back_to_the_original_values(scope, key_provider):
    tenant_id, conversation_id = scope
    vault = TokenVault(key_provider)
    text = "Lukas Berger wurde aufgenommen."

    result = Pseudonymizer(vault).apply(
        tenant_id, "conversation", conversation_id, text, [Span(0, 12, "PATIENT", 0.85, "custom")]
    )
    token = re.findall(r"[A-Z][A-Z_]*_[0-9A-F]{10}", result)[0]

    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) == "Lukas Berger"


def test_no_spans_leaves_the_text_untouched(scope, pseudonymizer):
    tenant_id, conversation_id = scope
    text = "Keine sensiblen Angaben."
    assert pseudonymizer.apply(tenant_id, "conversation", conversation_id, text, []) == text
