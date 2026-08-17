"""Seed the two dev tenants/users matching keycloak/realm-export.json's fixed
user IDs and tenant_id attributes.

Local dev and the manual E2E test (README) only -- never run against a
production database. Idempotent: re-running skips tenants that already exist.
"""

from __future__ import annotations

import uuid

from app.config import get_settings
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Tenant
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
        "keycloak_subject": "11111111-1111-4111-8111-111111111111",
        "email": "dr.mueller@clinic-a.example",
    },
    {
        "tenant_id": uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        "name": "Clinic B",
        "keycloak_realm": "chatgpt-proxy-dev-clinic-b",
        "keycloak_subject": "22222222-2222-4222-8222-222222222222",
        "email": "dr.klein@clinic-b.example",
    },
]


def main() -> int:
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
    for entry in DEV_TENANTS:
        with SessionLocal() as session:
            if session.get(Tenant, entry["tenant_id"]) is not None:
                print(f"{entry['name']}: already seeded, skipping")
                continue
            TenantRepository(session, key_provider).create(
                name=entry["name"],
                keycloak_realm=entry["keycloak_realm"],
                retention_days=30,
                tenant_id=entry["tenant_id"],
            )
            session.commit()

        with tenant_scoped_session(entry["tenant_id"]) as session:
            UserRepository(session).create(
                entry["tenant_id"],
                keycloak_subject=entry["keycloak_subject"],
                email=entry["email"],
                role="doctor",
            )
        print(f"{entry['name']}: seeded tenant {entry['tenant_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
