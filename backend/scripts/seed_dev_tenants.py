"""Seed the two dev tenants/users matching keycloak/realm-export.json's fixed
user IDs and tenant_id attributes.

Local dev and the manual E2E test (README) only -- never run against a
production database. Idempotent per tenant, branch, and user: re-running
skips whatever already exists.

Clinic A models a praxis with branches and a full staff structure; Clinic B
deliberately stays a single super_admin with no branches -- the living
praxis-single case every branch-aware feature must keep working for.
"""

from __future__ import annotations

import uuid

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.db.repositories.app_entitlement_repository import AppEntitlementRepository
from app.db.repositories.branch_repository import BranchRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Branch, Tenant
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider

DEV_TENANTS = [
    {
        "tenant_id": uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "name": "Clinic A",
        # tenants.keycloak_realm is UNIQUE (migration 0001); both dev tenants share
        # the one imported Keycloak realm ("chatgpt-proxy-dev", see
        # keycloak/realm-export.json), so this column can't literally hold that
        # shared value for both rows -- it's a per-tenant label within that realm,
        # not the realm name itself, for this single-dev-realm slice (Resolved
        # Design Ambiguity #1: this plan does not implement realm-per-tenant).
        "keycloak_realm": "chatgpt-proxy-dev-clinic-a",
        "branches": ["Hauptstandort", "Filiale Nord"],
        "users": [
            {
                "keycloak_subject": "11111111-1111-4111-8111-111111111111",
                "email": "dr.mueller@clinic-a.example",
                "role": "super_admin",
                "branch": "Hauptstandort",
            },
            {
                "keycloak_subject": "33333333-3333-4333-8333-333333333333",
                "email": "dr.weber@clinic-a.example",
                "role": "doctor",
                "branch": "Filiale Nord",
            },
            {
                "keycloak_subject": "44444444-4444-4444-8444-444444444444",
                "email": "empfang.schmidt@clinic-a.example",
                "role": "staff",
                "branch": "Hauptstandort",
            },
        ],
    },
    {
        "tenant_id": uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        "name": "Clinic B",
        "keycloak_realm": "chatgpt-proxy-dev-clinic-b",
        "branches": [],
        "users": [
            {
                "keycloak_subject": "22222222-2222-4222-8222-222222222222",
                "email": "dr.klein@clinic-b.example",
                "role": "super_admin",
                "branch": None,
            },
        ],
    },
]


def _grant_all_entitlements(tenant_id: uuid.UUID, app_ids: list[uuid.UUID]) -> None:
    """tenant_app_entitlements is app_ops-only for writes (app_runtime has
    SELECT-only there, migration 0007's "FastAPI never writes this table"),
    so -- unlike everything else in this script -- this can't go through
    tenant_scoped_session/app_runtime and needs its own app_ops connection.
    """
    engine = create_engine(get_settings().app_ops_database_url)
    try:
        with engine.begin() as conn:
            for app_id in app_ids:
                conn.execute(
                    text(
                        "INSERT INTO tenant_app_entitlements (id, tenant_id, app_id, granted_by, granted_at) "
                        "VALUES (:id, :tenant_id, :app_id, :granted_by, now()) "
                        "ON CONFLICT (tenant_id, app_id) DO UPDATE SET revoked_at = NULL"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_id,
                        "app_id": app_id,
                        "granted_by": "seed_dev_tenants",
                    },
                )
    finally:
        engine.dispose()


def main() -> int:
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
    for entry in DEV_TENANTS:
        tenant_id = entry["tenant_id"]

        with SessionLocal() as session:
            if session.get(Tenant, tenant_id) is None:
                TenantRepository(session, key_provider).create(
                    name=entry["name"],
                    keycloak_realm=entry["keycloak_realm"],
                    retention_days=30,
                    tenant_id=tenant_id,
                )
                session.commit()
                print(f"{entry['name']}: seeded tenant {tenant_id}")
            else:
                print(f"{entry['name']}: tenant exists, checking branches/users")

        with tenant_scoped_session(tenant_id) as session:
            branch_repo = BranchRepository(session)
            branches_by_name: dict[str, Branch] = {
                branch.name: branch for branch in branch_repo.list_for_tenant(tenant_id)
            }
            for branch_name in entry["branches"]:
                if branch_name not in branches_by_name:
                    branches_by_name[branch_name] = branch_repo.create(tenant_id, branch_name)
                    print(f"{entry['name']}: seeded branch {branch_name!r}")

            user_repo = UserRepository(session)
            for user_entry in entry["users"]:
                existing = user_repo.get_by_keycloak_subject(
                    tenant_id, user_entry["keycloak_subject"]
                )
                if existing is not None:
                    # Dev-only role/branch sync: a DB seeded before the RBAC migration
                    # has these users as 'doctor' with no branch; converge them to the
                    # declared values so re-running the seed is all a dev needs after
                    # upgrading.
                    declared_branch = (
                        branches_by_name[user_entry["branch"]].id
                        if user_entry["branch"] is not None
                        else None
                    )
                    if existing.role != user_entry["role"] or existing.branch_id != declared_branch:
                        user_repo.update(
                            tenant_id,
                            existing.id,
                            role=user_entry["role"],
                            branch_id=declared_branch,
                        )
                        print(
                            f"{entry['name']}: updated {user_entry['email']} "
                            f"-> role={user_entry['role']} branch={user_entry['branch']}"
                        )
                    continue
                branch = (
                    branches_by_name[user_entry["branch"]]
                    if user_entry["branch"] is not None
                    else None
                )
                user_repo.create(
                    tenant_id,
                    keycloak_subject=user_entry["keycloak_subject"],
                    email=user_entry["email"],
                    role=user_entry["role"],
                    branch_id=branch.id if branch is not None else None,
                )
                print(f"{entry['name']}: seeded user {user_entry['email']} ({user_entry['role']})")

            # Every dev tenant gets every catalog app, tenant-wide -- dev/demo
            # data should exercise every app, not just whatever migration
            # 0007's backfill happened to cover at the time it ran.
            apps_repo = AppEntitlementRepository(session)
            catalog_apps = apps_repo.list_catalog()
            _grant_all_entitlements(tenant_id, [app.id for app in catalog_apps])
            for app in catalog_apps:
                apps_repo.upsert_assignment(
                    tenant_id, app.id, branch_id=None, is_enabled=True, assigned_by=None
                )
            print(f"{entry['name']}: entitled + enabled {[app.key for app in catalog_apps]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
