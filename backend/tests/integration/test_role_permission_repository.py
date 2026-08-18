import uuid

from app.auth.permissions import DEFAULT_PERMISSIONS, Permission, resolve_permissions
from app.db.repositories.role_permission_repository import RolePermissionRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Tenant


def _create_tenant() -> uuid.UUID:
    with SessionLocal() as session:
        tenant = Tenant(name="RBAC Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30)
        session.add(tenant)
        session.commit()
        return tenant.id


def test_replace_for_tenant_round_trips_and_affects_resolution():
    tenant_id = _create_tenant()

    with tenant_scoped_session(tenant_id) as session:
        repo = RolePermissionRepository(session)
        repo.replace_for_tenant(
            tenant_id,
            [("doctor", str(Permission.CONVERSATIONS_READ_BRANCH), True)],
        )

        resolved = resolve_permissions(session, tenant_id, "doctor")
        assert Permission.CONVERSATIONS_READ_BRANCH in resolved

        # Full-replace semantics: a second PUT without the row removes the grant.
        repo.replace_for_tenant(tenant_id, [])
        assert resolve_permissions(session, tenant_id, "doctor") == DEFAULT_PERMISSIONS["doctor"]


def test_overrides_are_tenant_scoped():
    tenant_a = _create_tenant()
    tenant_b = _create_tenant()

    with tenant_scoped_session(tenant_a) as session:
        RolePermissionRepository(session).replace_for_tenant(
            tenant_a, [("staff", str(Permission.CONVERSATIONS_READ_ALL), True)]
        )

    with tenant_scoped_session(tenant_b) as session:
        assert resolve_permissions(session, tenant_b, "staff") == DEFAULT_PERMISSIONS["staff"]

    with tenant_scoped_session(tenant_a) as session:
        assert Permission.CONVERSATIONS_READ_ALL in resolve_permissions(session, tenant_a, "staff")
