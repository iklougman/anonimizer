import uuid

import pytest

from app.auth.jwt_validator import TokenClaims
from app.auth.tenant_resolver import UnknownTenantError, UnknownUserError, resolve_authenticated_user
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def _create_tenant_and_user(tmp_path, keycloak_subject="sub-1", role="doctor"):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Test Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=keycloak_subject, email="doc@example.com", role=role
        )
        user_id = user.id

    return tenant_id, user_id


def test_resolves_a_known_tenant_and_user(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    claims = TokenClaims(tenant_id=tenant_id, keycloak_subject="sub-1")

    result = resolve_authenticated_user(claims)

    assert result.tenant_id == tenant_id
    assert result.user_id == user_id
    assert result.role == "doctor"


def test_unknown_tenant_id_is_rejected():
    claims = TokenClaims(tenant_id=uuid.uuid4(), keycloak_subject="sub-1")
    with pytest.raises(UnknownTenantError):
        resolve_authenticated_user(claims)


def test_known_tenant_unknown_subject_is_rejected(tmp_path):
    tenant_id, _ = _create_tenant_and_user(tmp_path)
    claims = TokenClaims(tenant_id=tenant_id, keycloak_subject="someone-else")
    with pytest.raises(UnknownUserError):
        resolve_authenticated_user(claims)
