import os
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Tenant, TenantKey
from app.privacy_gateway.token_vault.key_provider import KeyProvider


class TenantRepository:
    def __init__(self, session: Session, key_provider: KeyProvider) -> None:
        self.session = session
        self.key_provider = key_provider

    def create(self, name: str, keycloak_realm: str, retention_days: int) -> Tenant:
        tenant = Tenant(name=name, keycloak_realm=keycloak_realm, retention_days=retention_days)
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
