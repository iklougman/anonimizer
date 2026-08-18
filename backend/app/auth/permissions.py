"""Role permission model: code-level defaults, per-tenant overrides, super_admin bypass.

Own-conversation read/write is deliberately NOT a permission — it is the base
capability of every active user, so no matrix configuration can ever lock a
doctor out of their own conversations.
"""
from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy.orm import Session

ROLE_SUPER_ADMIN = "super_admin"
ROLE_DOCTOR = "doctor"
ROLE_STAFF = "staff"

ALL_ROLES = (ROLE_SUPER_ADMIN, ROLE_DOCTOR, ROLE_STAFF)


class Permission(StrEnum):
    CONVERSATIONS_CREATE = "conversations:create"
    CONVERSATIONS_READ_BRANCH = "conversations:read:branch"
    CONVERSATIONS_READ_ALL = "conversations:read:all"
    CONVERSATIONS_DELETE_ANY = "conversations:delete:any"
    ADMIN_USERS_MANAGE = "admin:users:manage"
    ADMIN_BRANCHES_MANAGE = "admin:branches:manage"
    ADMIN_PERMISSIONS_MANAGE = "admin:permissions:manage"


# Only these roles/permissions appear in the admin matrix UI; the rest are structural.
MATRIX_ROLES = (ROLE_DOCTOR, ROLE_STAFF)
MATRIX_PERMISSIONS = (Permission.CONVERSATIONS_READ_BRANCH, Permission.CONVERSATIONS_READ_ALL)

# Defaults reproduce the pre-RBAC behavior exactly: doctors and staff chat with
# their own conversations only. staff getting CONVERSATIONS_CREATE is a product
# decision (full chat for reception/personnel), not an oversight.
DEFAULT_PERMISSIONS: dict[str, frozenset[Permission]] = {
    ROLE_DOCTOR: frozenset({Permission.CONVERSATIONS_CREATE}),
    ROLE_STAFF: frozenset({Permission.CONVERSATIONS_CREATE}),
}

# super_admin bypass: always every permission, unconditionally. Lock-out is
# impossible by construction; the matrix table's CHECK constraint keeps
# super_admin rows out of the database entirely.
ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)


def resolve_permissions(
    session: Session, tenant_id: uuid.UUID, role: str
) -> frozenset[Permission]:
    """Effective permissions for a role in a tenant: defaults ± sparse overrides."""
    if role == ROLE_SUPER_ADMIN:
        return ALL_PERMISSIONS

    # Imported here to avoid a module cycle (repositories import models, and the
    # auth dependency chain imports this module very early).
    from app.db.repositories.role_permission_repository import RolePermissionRepository

    effective = set(DEFAULT_PERMISSIONS.get(role, frozenset()))
    for row in RolePermissionRepository(session).list_for_role(tenant_id, role):
        try:
            permission = Permission(row.permission)
        except ValueError:
            # A row naming a permission this code version doesn't know (e.g. written
            # by a newer deployment) is ignored rather than crashing every request.
            continue
        if row.granted:
            effective.add(permission)
        else:
            effective.discard(permission)
    return frozenset(effective)
