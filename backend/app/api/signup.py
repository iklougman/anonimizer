"""POST /api/signup: public, unauthenticated tenant self-onboarding.

Every other write path in this codebase runs inside a tenant that already
exists (get_db_session requires an already-resolved AuthenticatedUser, which
in turn requires a tenant). This endpoint is the one place that *creates* a
tenant from nothing, so it cannot use that dependency -- it follows
scripts/provision_e2e_tenant.py's pattern instead: a plain SessionLocal() for
the tenant-creation and entitlement-grant steps (no tenant context exists yet
to scope a session to), then a fresh tenant_scoped_session(tenant_id) once the
tenant exists, for the super_admin user insert.

Three independent DB commits happen here (tenant, entitlement grant, user) --
deliberately not one transaction, mirroring provision_e2e_tenant.py's own
create(). If the user insert fails after the tenant/entitlement steps already
committed, the Keycloak user is compensated (deleted) but the tenant row is
intentionally left in place rather than attempting a distributed rollback;
an orphaned tenant with no users is inert (nothing can ever authenticate into
it) and cheap to sweep up out-of-band, unlike trying to undo a already-
committed cross-transaction write from here.
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.rate_limit import RateLimitExceededError, check_rate_limit
from app.api.schemas import TenantSignupIn, TenantSignupOut
from app.auth.dependencies import get_keycloak_admin_client
from app.config import get_settings
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.keycloak_admin.client import KeycloakAdminClient, KeycloakAdminConflictError, KeycloakAdminError
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider

router = APIRouter(prefix="/api", tags=["signup"])

# Fixed for every self-signup tenant -- ops can raise this for a specific
# tenant later the same way they do everything else tenant-specific, through
# the Django ops-admin console; there is no signup-time input for it.
_DEFAULT_RETENTION_DAYS = 30


@router.post("/signup", response_model=TenantSignupOut, status_code=201)
def signup(
    body: TenantSignupIn,
    request: Request,
    admin_client: KeycloakAdminClient | None = Depends(get_keycloak_admin_client),
) -> TenantSignupOut:
    settings = get_settings()

    try:
        check_rate_limit(
            request.client.host if request.client else "unknown",
            settings.signup_rate_limit_per_hour,
        )
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=429, detail="too many signup attempts, try again later"
        ) from exc

    if admin_client is None:
        # Same "degrade, don't block" posture as POST /api/admin/users
        # (get_keycloak_admin_client's own docstring): without a configured
        # Keycloak admin client there is no way to provision a login for the
        # new user, so self-signup simply is not offered rather than silently
        # creating a tenant nobody can ever sign into.
        raise HTTPException(
            status_code=501,
            detail="self-signup is not available -- Keycloak provisioning is not configured",
        )

    tenant_id = uuid.uuid4()
    key_provider = FileSecretKeyProvider(settings.master_key_path)

    with SessionLocal() as session:
        TenantRepository(session, key_provider).create(
            name=body.practice_name,
            keycloak_realm=f"self-signup-{tenant_id.hex[:12]}",
            retention_days=_DEFAULT_RETENTION_DAYS,
            tenant_id=tenant_id,
        )
        session.commit()

    with SessionLocal() as session:
        session.execute(
            sa.text("SELECT grant_default_entitlement_on_signup(:tid)"),
            {"tid": str(tenant_id)},
        )
        session.commit()

    try:
        subject = admin_client.create_user(
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            tenant_id=str(tenant_id),
            required_actions=["VERIFY_EMAIL"],
        )
        admin_client.set_password(subject, body.password, temporary=False)
    except KeycloakAdminConflictError as exc:
        raise HTTPException(
            status_code=409, detail="a Keycloak user with this email already exists"
        ) from exc
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=502, detail="identity provider unavailable") from exc

    verification_email_sent = True
    try:
        admin_client.send_required_actions_email(subject, ["VERIFY_EMAIL"])
    except KeycloakAdminError:
        # Best-effort, same posture as the client's own send_required_actions_email
        # docstring: dev/self-hosted Keycloak commonly has no SMTP configured.
        # The signup itself still succeeds; the frontend can tell the user to
        # request a fresh verification email later.
        verification_email_sent = False

    try:
        with tenant_scoped_session(tenant_id) as session:
            UserRepository(session).create(
                tenant_id,
                keycloak_subject=subject,
                email=body.email,
                role="super_admin",
                branch_id=None,
            )
    except Exception as exc:
        # Compensate the Keycloak-side create so a failed signup does not
        # leave a login nobody in our DB can ever be resolved for.
        admin_client.delete_user(subject)
        raise HTTPException(status_code=500, detail="signup failed") from exc

    return TenantSignupOut(tenant_id=tenant_id, verification_email_sent=verification_email_sent)
