import os
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Tenant, TenantKey
from app.privacy_gateway.token_vault.key_provider import KeyProvider


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

        return tenant

    def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return self.session.get(Tenant, tenant_id)
