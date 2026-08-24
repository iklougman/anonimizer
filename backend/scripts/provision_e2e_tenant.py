"""Provision (or clean up) a uniquely-named, throwaway tenant for one e2e
test run: a tenant, one branch, and three users (super_admin, doctor,
staff) all on that branch, entitled+assigned for the "anonymization" app.

Never run against a production database. Each invocation of `create`
generates a fresh, unique tenant -- unlike seed_dev_tenants.py's fixed,
upserted dev tenants, this script exists specifically so parallel/repeated
e2e runs never collide. The two named dev tenants stay reserved for manual
testing; e2e tests must never touch them.

Usage:
    python scripts/provision_e2e_tenant.py create
    python scripts/provision_e2e_tenant.py cleanup --tenant-id <uuid>

`create` prints one JSON object to stdout: tenant_id, branch_id, and a
users object keyed by role, each with user_id/email/password/keycloak_subject.
"""

from __future__ import annotations

import argparse
import json
import secrets
import uuid

import sqlalchemy as sa

from app.config import get_settings
from app.db.repositories.branch_repository import BranchRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.keycloak_admin.client import KeycloakAdminClient
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider

ROLES = ("super_admin", "doctor", "staff")


class _FakeKeycloakAdminClient:
    """In-process stand-in used only by this script's own test suite
    (--fake-keycloak-client), never by a real e2e run. Avoids depending on a
    real Keycloak Admin API connection for a backend-only integration test."""

    def create_user(self, email: str, first_name: str, last_name: str, tenant_id: str) -> str:
        return str(uuid.uuid4())

    def set_password(self, subject: str, password: str, temporary: bool = False) -> None:
        pass

    def delete_user(self, subject: str) -> None:
        pass


def _build_keycloak_client(use_fake: bool) -> KeycloakAdminClient | _FakeKeycloakAdminClient:
    if use_fake:
        return _FakeKeycloakAdminClient()
    settings = get_settings()
    if not settings.keycloak_admin_client_id or not settings.keycloak_admin_client_secret:
        raise SystemExit(
            "KEYCLOAK_ADMIN_CLIENT_ID/KEYCLOAK_ADMIN_CLIENT_SECRET must be set "
            "to provision real Keycloak users (or pass --fake-keycloak-client "
            "for a DB-only test run)"
        )
    return KeycloakAdminClient(
        base_url=settings.keycloak_admin_base_url,
        realm=settings.keycloak_admin_realm,
        client_id=settings.keycloak_admin_client_id,
        client_secret=settings.keycloak_admin_client_secret,
    )


def _grant_anonymization_entitlement(tenant_id: uuid.UUID) -> None:
    """Mirrors tests/conftest.py::grant_app_entitlement exactly: a direct
    create_engine(database_url) connection (the migration-owner role, not
    app_runtime/app_ops), since TenantRepository.create() only auto-creates
    the tenant-wide *assignment* row, never the *entitlement* row -- the dev
    seed tenants get entitled out-of-band via the ops_admin console; an
    ephemeral, fully-automated tenant has no such manual step."""
    engine = sa.create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO tenant_app_entitlements (id, tenant_id, app_id, granted_by) "
                "SELECT gen_random_uuid(), :tenant_id, id, 'e2e-provisioning' FROM apps WHERE key = 'anonymization' "
                "ON CONFLICT (tenant_id, app_id) DO UPDATE SET revoked_at = NULL"
            ),
            {"tenant_id": str(tenant_id)},
        )
    engine.dispose()


def create(use_fake_keycloak_client: bool) -> int:
    run_marker = uuid.uuid4().hex[:12]
    tenant_id = uuid.uuid4()
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
    keycloak_client = _build_keycloak_client(use_fake_keycloak_client)

    with SessionLocal() as session:
        TenantRepository(session, key_provider).create(
            name=f"E2E {run_marker}",
            keycloak_realm=f"e2e-{run_marker}",
            retention_days=1,
            tenant_id=tenant_id,
        )
        session.commit()

    _grant_anonymization_entitlement(tenant_id)

    users_payload: dict[str, dict[str, str]] = {}
    with tenant_scoped_session(tenant_id) as session:
        branch = BranchRepository(session).create(tenant_id, f"E2E Branch {run_marker}")
        branch_id = branch.id

        for role in ROLES:
            email = f"e2e-{role}-{run_marker}@example.test"
            password = secrets.token_urlsafe(16)
            subject = keycloak_client.create_user(
                email=email, first_name="E2E", last_name=role.replace("_", " ").title(),
                tenant_id=str(tenant_id),
            )
            keycloak_client.set_password(subject, password, temporary=False)
            user = UserRepository(session).create(
                tenant_id, keycloak_subject=subject, email=email, role=role, branch_id=branch_id,
            )
            users_payload[role] = {
                "user_id": str(user.id),
                "email": email,
                "password": password,
                "keycloak_subject": subject,
            }

    print(json.dumps({
        "tenant_id": str(tenant_id),
        "branch_id": str(branch_id),
        "users": users_payload,
    }))
    return 0


def cleanup(tenant_id: uuid.UUID, use_fake_keycloak_client: bool) -> int:
    keycloak_client = _build_keycloak_client(use_fake_keycloak_client)

    with tenant_scoped_session(tenant_id) as session:
        users = session.execute(
            sa.text("SELECT keycloak_subject FROM users WHERE tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        ).scalars().all()
        for subject in users:
            keycloak_client.delete_user(subject)

        # token_mappings/llm_requests/audit_events all carry hard (non-cascading)
        # composite FKs to conversations (app/models/token_mapping.py,
        # llm_request.py, audit_event.py) -- app/api/chat.py writes llm_requests
        # on every successful turn and audit_events on every leakage/unresolved-
        # token error, so any tenant that ran a real e2e suite has rows here.
        # token_mappings also FKs to tenant_keys via dek_id, so it must clear
        # before the tenant_keys delete below too -- doing it here (ahead of
        # conversations) satisfies both.
        session.execute(sa.text("DELETE FROM token_mappings WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM llm_requests WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM audit_events WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM messages WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM conversations WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM users WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        # tenant_role_permissions FKs directly to tenants.id (migration 0006) --
        # PUT /api/admin/permissions (app/api/admin.py) writes rows here via
        # RolePermissionRepository.replace_for_tenant whenever an admin saves a
        # permission override, so any tenant that had one written fails the
        # tenants delete below with a ForeignKeyViolation unless cleared first.
        session.execute(sa.text("DELETE FROM tenant_role_permissions WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        # tenant_app_assignments FKs to branches via branch_id (nullable; always
        # NULL for assignments this script creates, but deleting it before
        # branches defensively holds even if a future change adds a
        # branch-scoped assignment).
        session.execute(sa.text("DELETE FROM tenant_app_assignments WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM branches WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})

    engine = sa.create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM tenant_app_entitlements WHERE tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )
        connection.execute(
            sa.text("DELETE FROM tenant_keys WHERE tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )
        connection.execute(
            sa.text("DELETE FROM tenants WHERE id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )
    engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("create").add_argument(
        "--fake-keycloak-client", action="store_true",
        help="Use an in-process fake instead of a real Keycloak Admin API connection (test use only).",
    )
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--tenant-id", required=True)
    cleanup_parser.add_argument("--fake-keycloak-client", action="store_true")

    args = parser.parse_args()
    if args.command == "create":
        return create(use_fake_keycloak_client=args.fake_keycloak_client)
    return cleanup(uuid.UUID(args.tenant_id), use_fake_keycloak_client=args.fake_keycloak_client)


if __name__ == "__main__":
    raise SystemExit(main())
