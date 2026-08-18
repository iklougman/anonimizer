from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.schemas import (
    AdminUserCreateIn,
    AdminUserCreateOut,
    AdminUserOut,
    AdminUserUpdateIn,
    BranchCreateIn,
    BranchOut,
    BranchUpdateIn,
    PermissionMatrixIn,
    PermissionMatrixOut,
)
from app.auth.dependencies import get_db_session, get_keycloak_admin_client, require_permission
from app.auth.permissions import (
    ALL_ROLES,
    DEFAULT_PERMISSIONS,
    MATRIX_PERMISSIONS,
    MATRIX_ROLES,
    Permission,
)
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.branch_repository import BranchInUseError, BranchRepository
from app.db.repositories.role_permission_repository import RolePermissionRepository
from app.db.repositories.user_repository import UserRepository
from app.keycloak_admin.client import KeycloakAdminClient, KeycloakAdminConflictError, KeycloakAdminError

router = APIRouter(prefix="/api/admin", tags=["admin"])


# --- Branches ---------------------------------------------------------------


@router.get("/branches", response_model=list[BranchOut])
def list_branches(
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_BRANCHES_MANAGE)),
    session: Session = Depends(get_db_session),
) -> list[BranchOut]:
    repo = BranchRepository(session)
    return [
        BranchOut(
            id=branch.id,
            name=branch.name,
            user_count=repo.count_users(user.tenant_id, branch.id),
            created_at=branch.created_at,
        )
        for branch in repo.list_for_tenant(user.tenant_id)
    ]


@router.post("/branches", response_model=BranchOut, status_code=201)
def create_branch(
    body: BranchCreateIn,
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_BRANCHES_MANAGE)),
    session: Session = Depends(get_db_session),
) -> BranchOut:
    repo = BranchRepository(session)
    try:
        branch = repo.create(user.tenant_id, body.name)
    except Exception as exc:  # unique-constraint violation on (tenant_id, name)
        raise HTTPException(status_code=409, detail="a branch with this name already exists") from exc
    return BranchOut(id=branch.id, name=branch.name, user_count=0, created_at=branch.created_at)


@router.patch("/branches/{branch_id}", response_model=BranchOut)
def update_branch(
    branch_id: uuid.UUID,
    body: BranchUpdateIn,
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_BRANCHES_MANAGE)),
    session: Session = Depends(get_db_session),
) -> BranchOut:
    repo = BranchRepository(session)
    if repo.get(user.tenant_id, branch_id) is None:
        raise HTTPException(status_code=404, detail="branch not found")
    try:
        repo.rename(user.tenant_id, branch_id, body.name)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="a branch with this name already exists") from exc
    branch = repo.get(user.tenant_id, branch_id)
    return BranchOut(
        id=branch.id, name=branch.name, user_count=repo.count_users(user.tenant_id, branch_id), created_at=branch.created_at
    )


@router.delete("/branches/{branch_id}", status_code=204)
def delete_branch(
    branch_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_BRANCHES_MANAGE)),
    session: Session = Depends(get_db_session),
) -> Response:
    repo = BranchRepository(session)
    if repo.get(user.tenant_id, branch_id) is None:
        raise HTTPException(status_code=404, detail="branch not found")
    try:
        repo.delete(user.tenant_id, branch_id)
    except BranchInUseError as exc:
        raise HTTPException(
            status_code=409, detail="branch still has users assigned; reassign them first"
        ) from exc
    return Response(status_code=204)


# --- Users --------------------------------------------------------------


def _to_admin_user_out(user_row) -> AdminUserOut:
    return AdminUserOut(
        id=user_row.id,
        email=user_row.email,
        role=user_row.role,
        branch_id=user_row.branch_id,
        is_active=user_row.is_active,
        created_at=user_row.created_at,
    )


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_USERS_MANAGE)),
    session: Session = Depends(get_db_session),
) -> list[AdminUserOut]:
    return [_to_admin_user_out(u) for u in UserRepository(session).list_for_tenant(user.tenant_id)]


@router.post("/users", response_model=AdminUserCreateOut, status_code=201)
def create_user(
    body: AdminUserCreateIn,
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_USERS_MANAGE)),
    session: Session = Depends(get_db_session),
    admin_client: KeycloakAdminClient | None = Depends(get_keycloak_admin_client),
) -> AdminUserCreateOut:
    if body.role not in ALL_ROLES:
        raise HTTPException(status_code=422, detail=f"unknown role {body.role!r}")

    user_repo = UserRepository(session)
    invite_sent = False

    if body.keycloak_subject is not None:
        # Link mode: the account already exists in Keycloak (created via the
        # console, with the tenant_id attribute set manually) -- the
        # documented fallback when no admin client is configured.
        subject = body.keycloak_subject
    elif admin_client is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "Keycloak provisioning is not configured; create the user in the "
                "Keycloak console with a tenant_id attribute and retry with "
                "keycloak_subject set (link mode)"
            ),
        )
    else:
        try:
            subject = admin_client.create_user(
                email=body.email,
                first_name=body.first_name or "",
                last_name=body.last_name or "",
                tenant_id=str(user.tenant_id),
            )
        except KeycloakAdminConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail="a Keycloak user with this email already exists; use link mode instead",
            ) from exc
        except KeycloakAdminError as exc:
            raise HTTPException(status_code=502, detail="identity provider unavailable") from exc

        try:
            admin_client.send_invite(subject)
            invite_sent = True
        except KeycloakAdminError:
            invite_sent = False

    try:
        created = user_repo.create(
            user.tenant_id,
            keycloak_subject=subject,
            email=body.email,
            role=body.role,
            branch_id=body.branch_id,
        )
    except Exception as exc:
        if body.keycloak_subject is None and admin_client is not None:
            # Compensate the Keycloak-side create so a DB failure doesn't
            # leave an orphaned Keycloak account with no app-side record.
            admin_client.delete_user(subject)
        raise HTTPException(
            status_code=409, detail="a user for this Keycloak subject already exists"
        ) from exc

    return AdminUserCreateOut(**_to_admin_user_out(created).model_dump(), invite_email_sent=invite_sent)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdateIn,
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_USERS_MANAGE)),
    session: Session = Depends(get_db_session),
    admin_client: KeycloakAdminClient | None = Depends(get_keycloak_admin_client),
) -> AdminUserOut:
    repo = UserRepository(session)
    target = repo.get(user.tenant_id, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    if body.role is not None and body.role not in ALL_ROLES:
        raise HTTPException(status_code=422, detail=f"unknown role {body.role!r}")

    demoting = target.role == "super_admin" and body.role is not None and body.role != "super_admin"
    deactivating = target.role == "super_admin" and body.is_active is False and target.is_active
    if demoting or deactivating:
        # Unconditional, even with other super_admins around: this admin panel
        # is not the place to change your own admin status, so the failure
        # mode is always "use a different account", never "it depends how
        # many admins are left".
        if user_id == user.user_id:
            raise HTTPException(
                status_code=409, detail="cannot demote or deactivate your own super_admin account"
            )
        if repo.count_active_super_admins(user.tenant_id) <= 1:
            raise HTTPException(
                status_code=409,
                detail="cannot demote or deactivate the last active super_admin",
            )

    branch_id = body.branch_id if "branch_id" in body.model_fields_set else ...
    repo.update(user.tenant_id, user_id, role=body.role, branch_id=branch_id, is_active=body.is_active)

    if body.is_active is not None and admin_client is not None:
        try:
            admin_client.set_enabled(target.keycloak_subject, body.is_active)
        except KeycloakAdminError:
            # DB is_active is authoritative (the resolver rejects the next
            # request regardless); Keycloak-side sync is best-effort.
            pass

    updated = repo.get(user.tenant_id, user_id)
    return _to_admin_user_out(updated)


# --- Permission matrix --------------------------------------------------


@router.get("/permissions", response_model=PermissionMatrixOut)
def get_permission_matrix(
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_PERMISSIONS_MANAGE)),
    session: Session = Depends(get_db_session),
) -> PermissionMatrixOut:
    overrides = {
        (row.role, row.permission): row.granted
        for row in RolePermissionRepository(session).list_for_tenant(user.tenant_id)
    }
    matrix: dict[str, dict[str, bool]] = {}
    for role in MATRIX_ROLES:
        matrix[role] = {}
        for permission in MATRIX_PERMISSIONS:
            default = permission in DEFAULT_PERMISSIONS.get(role, frozenset())
            matrix[role][str(permission)] = overrides.get((role, str(permission)), default)

    return PermissionMatrixOut(
        roles=list(MATRIX_ROLES),
        permissions=[str(p) for p in MATRIX_PERMISSIONS],
        defaults={
            role: [str(p) for p in DEFAULT_PERMISSIONS.get(role, frozenset()) if p in MATRIX_PERMISSIONS]
            for role in MATRIX_ROLES
        },
        matrix=matrix,
    )


@router.put("/permissions", response_model=PermissionMatrixOut)
def put_permission_matrix(
    body: PermissionMatrixIn,
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_PERMISSIONS_MANAGE)),
    session: Session = Depends(get_db_session),
) -> PermissionMatrixOut:
    matrix_permission_values = {str(p) for p in MATRIX_PERMISSIONS}
    rows: list[tuple[str, str, bool]] = []
    for role, permissions in body.matrix.items():
        if role not in MATRIX_ROLES:
            raise HTTPException(status_code=422, detail=f"unknown role {role!r}")
        for permission, granted in permissions.items():
            if permission not in matrix_permission_values:
                raise HTTPException(status_code=422, detail=f"unknown permission {permission!r}")
            default = permission in {str(p) for p in DEFAULT_PERMISSIONS.get(role, frozenset())}
            # Only store rows that diverge from the default -- an empty table
            # stays a valid, fully-defaulted matrix (see permissions.py).
            if granted != default:
                rows.append((role, permission, granted))

    RolePermissionRepository(session).replace_for_tenant(user.tenant_id, rows)
    return get_permission_matrix(user=user, session=session)
