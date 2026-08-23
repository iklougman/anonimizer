from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.jwt_validator import InvalidTokenError, JWTValidator, KeycloakUnreachableError
from app.auth.permissions import Permission
from app.auth.tenant_resolver import (
    AuthenticatedUser,
    InactiveUserError,
    UnknownTenantError,
    UnknownUserError,
    resolve_authenticated_user,
)
from app.config import get_settings
from app.db.repositories.app_entitlement_repository import AppEntitlementRepository
from app.db.session import tenant_scoped_session
from app.keycloak_admin.client import KeycloakAdminClient


@lru_cache(maxsize=1)
def get_jwt_validator() -> JWTValidator:
    settings = get_settings()
    return JWTValidator(
        jwks_url=settings.keycloak_jwks_url,
        issuer=settings.keycloak_issuer_url,
        audience=settings.keycloak_audience,
    )


def get_user_resolver():
    """A thin, override-able indirection so `get_current_user` is unit-testable
    without a real Postgres connection -- `resolve_authenticated_user` itself is
    covered by its own integration test."""
    return resolve_authenticated_user


def get_current_user(
    request: Request,
    validator: JWTValidator = Depends(get_jwt_validator),
    resolve_user=Depends(get_user_resolver),
) -> AuthenticatedUser:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()

    try:
        claims = validator.validate(token)
    except KeycloakUnreachableError as exc:
        raise HTTPException(status_code=503, detail="authentication service unavailable") from exc
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc

    try:
        return resolve_user(claims)
    except (UnknownTenantError, UnknownUserError) as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    except InactiveUserError as exc:
        # 403, not 401: the identity is valid, the account is disabled. The
        # frontend can show a real message instead of bouncing to login.
        raise HTTPException(status_code=403, detail="account deactivated") from exc


def require_permission(permission: Permission):
    """Dependency factory gating an endpoint on one permission.

    super_admin passes every check by construction (resolve_permissions returns
    ALL_PERMISSIONS for it), so admin lock-out is impossible.
    """

    def _check(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if permission not in user.permissions:
            raise HTTPException(status_code=403, detail="insufficient permissions")
        return user

    return _check


def require_app_entitlement(app_key: str):
    """Dependency factory gating an endpoint on the tenant being entitled to
    (ops-granted) and having enabled (tenant-admin-assigned) a given app.

    Shaped like `require_permission` so it composes identically in an endpoint
    signature, but checked separately since it's not a role/permission
    question -- it's "is this tenant subscribed to this app at all".
    """

    def _check(
        user: AuthenticatedUser = Depends(get_current_user),
        session: Session = Depends(get_db_session),
    ) -> AuthenticatedUser:
        if not AppEntitlementRepository(session).is_entitled_and_assigned(
            user.tenant_id, app_key, user.branch_id
        ):
            raise HTTPException(
                status_code=403, detail=f"tenant is not entitled to the '{app_key}' app"
            )
        return user

    return _check


@lru_cache(maxsize=1)
def get_keycloak_admin_client() -> KeycloakAdminClient | None:
    """None when unconfigured -- POST /api/admin/users then only supports
    "link an existing Keycloak subject" mode. Overridable in tests, matching
    the get_user_resolver precedent, so admin API tests can exercise the
    provisioning path with a fake client instead of a real Keycloak."""
    settings = get_settings()
    if not settings.keycloak_admin_client_id or not settings.keycloak_admin_client_secret:
        return None
    return KeycloakAdminClient(
        base_url=settings.keycloak_admin_base_url,
        realm=settings.keycloak_admin_realm,
        client_id=settings.keycloak_admin_client_id,
        client_secret=settings.keycloak_admin_client_secret,
    )


def get_db_session(user: AuthenticatedUser = Depends(get_current_user)) -> Iterator[Session]:
    """Request-scoped, tenant-RLS-bound session for conversation/message repositories.

    Separate from the sessions `TokenVault` opens internally inside `Pipeline.sanitize`/
    `Pipeline.deanonymize`, which manage their own transactions per call.
    """
    with tenant_scoped_session(user.tenant_id) as session:
        yield session
