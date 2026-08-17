from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.auth.jwt_validator import TokenClaims
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Tenant


class UnknownTenantError(Exception):
    """The token's tenant_id claim does not match any provisioned tenant."""


class UnknownUserError(Exception):
    """The token's subject does not match any user provisioned for that tenant."""


@dataclass(frozen=True)
class AuthenticatedUser:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: str


def resolve_authenticated_user(claims: TokenClaims) -> AuthenticatedUser:
    """ADR-0021: the JWT's tenant_id claim is never trusted transitively -- it must
    resolve to a real tenant, and the subject must resolve to a real user of that
    tenant, before any tenant-scoped code runs."""
    with SessionLocal() as session:
        tenant = session.get(Tenant, claims.tenant_id)
    if tenant is None:
        raise UnknownTenantError(f"no tenant provisioned for tenant_id={claims.tenant_id}")

    with tenant_scoped_session(claims.tenant_id) as session:
        user = UserRepository(session).get_by_keycloak_subject(
            claims.tenant_id, claims.keycloak_subject
        )
    if user is None:
        raise UnknownUserError(
            f"no user provisioned for keycloak_subject={claims.keycloak_subject!r} "
            f"in tenant {claims.tenant_id}"
        )

    return AuthenticatedUser(tenant_id=claims.tenant_id, user_id=user.id, role=user.role)
