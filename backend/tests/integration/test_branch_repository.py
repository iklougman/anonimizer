import uuid

import pytest

from app.db.repositories.branch_repository import BranchInUseError, BranchRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Tenant


def _create_tenant() -> uuid.UUID:
    with SessionLocal() as session:
        tenant = Tenant(name="Branch Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30)
        session.add(tenant)
        session.commit()
        return tenant.id


def test_create_list_rename_delete_roundtrip():
    tenant_id = _create_tenant()

    with tenant_scoped_session(tenant_id) as session:
        repo = BranchRepository(session)
        created = repo.create(tenant_id, "Filiale Nord")
        assert repo.get(tenant_id, created.id).name == "Filiale Nord"

        repo.rename(tenant_id, created.id, "Filiale Ost")
        assert [b.name for b in repo.list_for_tenant(tenant_id)] == ["Filiale Ost"]

        repo.delete(tenant_id, created.id)
        assert repo.list_for_tenant(tenant_id) == []


def test_delete_refuses_while_users_are_assigned():
    tenant_id = _create_tenant()

    with tenant_scoped_session(tenant_id) as session:
        repo = BranchRepository(session)
        branch = repo.create(tenant_id, "Hauptstandort")
        user = UserRepository(session).create(
            tenant_id,
            keycloak_subject=f"sub-{uuid.uuid4()}",
            email="doc@example.com",
            role="doctor",
            branch_id=branch.id,
        )

        with pytest.raises(BranchInUseError):
            repo.delete(tenant_id, branch.id)

        # After reassignment the delete goes through.
        UserRepository(session).update(tenant_id, user.id, branch_id=None)
        repo.delete(tenant_id, branch.id)
        assert repo.list_for_tenant(tenant_id) == []


def test_branches_are_invisible_across_tenants():
    tenant_a = _create_tenant()
    tenant_b = _create_tenant()

    with tenant_scoped_session(tenant_a) as session:
        branch = BranchRepository(session).create(tenant_a, "Nur A")
        branch_id = branch.id

    with tenant_scoped_session(tenant_b) as session:
        repo = BranchRepository(session)
        assert repo.get(tenant_a, branch_id) is None
        assert repo.list_for_tenant(tenant_a) == []
