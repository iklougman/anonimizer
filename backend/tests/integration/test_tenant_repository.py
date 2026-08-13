import uuid

import sqlalchemy as sa

from app.db.repositories.tenant_repository import TenantRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import TenantKey
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def test_create_tenant_provisions_a_dek(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    with SessionLocal() as session:
        repo = TenantRepository(session, key_provider)
        tenant = repo.create(
            name="Test Clinic",
            keycloak_realm=f"realm-{uuid.uuid4()}",
            retention_days=30,
        )
        session.commit()
        tenant_id = tenant.id

    assert tenant_id is not None

    with tenant_scoped_session(tenant_id) as session:
        tenant_key = session.execute(
            sa.select(TenantKey).where(TenantKey.tenant_id == tenant_id)
        ).scalar_one()
        assert tenant_key.key_version == 1
        assert key_provider.unwrap_dek(tenant_key.wrapped_dek) is not None


def test_get_returns_none_for_unknown_tenant():
    with SessionLocal() as session:
        repo = TenantRepository(session, key_provider=None)
        assert repo.get(uuid.uuid4()) is None
