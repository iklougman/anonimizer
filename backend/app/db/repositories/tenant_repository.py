import os
import uuid

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import App, Tenant, TenantAppAssignment, TenantKey
from app.privacy_gateway.token_vault.key_provider import KeyProvider

# `tenant_app_entitlements` (the ops-controlled subscription grant) is
# deliberately NOT written here: app_runtime has read-only access to that
# table by design (migration 0007) -- only the internal Django ops-admin
# service (via app_ops) may grant an entitlement. Pre-creating a tenant-wide
# *assignment* here (app_runtime has full CRUD on tenant_app_assignments) is
# still worthwhile: once ops grants the "anonymization" entitlement through
# the Django admin, the tenant is immediately enabled with no second admin
# action required on the tenant-admin side.
_BASE_APP_KEY = "anonymization"


class TenantRepository:
    """The one repository that is deliberately NOT tenant-scoped.

    Every other repository takes `tenant_id` as its mandatory first argument and is
    driven from a `tenant_scoped_session()`. This one cannot be: creating a tenant is
    what brings a tenant into existence, so there is no tenant context to open a scoped
    session with at the time `create()` runs. Callers therefore pass a plain
    `SessionLocal()` session.

    `create()` sets `app.current_tenant_id` itself, immediately after flushing the new
    `tenants` row and before inserting the first `tenant_keys` row — `tenant_keys` is
    tenant-scoped and RLS-protected, so its INSERT would otherwise fail the
    `tenant_isolation` WITH CHECK policy. Do not "simplify" this by moving tenant
    creation into `tenant_scoped_session()`; the chicken-and-egg ordering is the point.
    """

    def __init__(self, session: Session, key_provider: KeyProvider) -> None:
        self.session = session
        self.key_provider = key_provider

    def create(
        self,
        name: str,
        keycloak_realm: str,
        retention_days: int,
        tenant_id: uuid.UUID | None = None,
    ) -> Tenant:
        tenant = Tenant(
            id=tenant_id if tenant_id is not None else uuid.uuid4(),
            name=name,
            keycloak_realm=keycloak_realm,
            retention_days=retention_days,
        )
        self.session.add(tenant)
        self.session.flush()

        self.session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant.id)},
        )

        raw_dek = os.urandom(32)
        wrapped_dek = self.key_provider.wrap_dek(raw_dek)
        tenant_key = TenantKey(tenant_id=tenant.id, wrapped_dek=wrapped_dek, key_version=1)
        self.session.add(tenant_key)
        self.session.flush()

        base_app = self.session.execute(
            sa.select(App.id).where(App.key == _BASE_APP_KEY)
        ).scalar_one_or_none()
        if base_app is not None:
            self.session.add(
                TenantAppAssignment(
                    tenant_id=tenant.id, app_id=base_app, branch_id=None, is_enabled=True, assigned_by=None
                )
            )
            self.session.flush()

        return tenant

    def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return self.session.get(Tenant, tenant_id)
