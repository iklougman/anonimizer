"""resolve_permissions: code-level defaults ± sparse per-tenant overrides.

The repository is stubbed — the DB-backed path is covered by
tests/integration/test_role_permission_repository.py.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from app.auth.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_PERMISSIONS,
    Permission,
    resolve_permissions,
)

TENANT_ID = uuid.uuid4()


def _resolve_with_rows(role, rows):
    class _StubRepo:
        def __init__(self, session):
            pass

        def list_for_role(self, tenant_id, role):
            return rows

    with patch(
        "app.db.repositories.role_permission_repository.RolePermissionRepository", _StubRepo
    ):
        return resolve_permissions(session=None, tenant_id=TENANT_ID, role=role)


def _row(permission, granted):
    return SimpleNamespace(permission=str(permission), granted=granted)


def test_doctor_defaults_are_own_chat_only():
    assert _resolve_with_rows("doctor", []) == DEFAULT_PERMISSIONS["doctor"]
    assert Permission.CONVERSATIONS_CREATE in DEFAULT_PERMISSIONS["doctor"]
    assert Permission.CONVERSATIONS_READ_ALL not in DEFAULT_PERMISSIONS["doctor"]


def test_staff_defaults_include_full_chat():
    assert Permission.CONVERSATIONS_CREATE in _resolve_with_rows("staff", [])


def test_grant_override_adds_a_permission():
    resolved = _resolve_with_rows(
        "doctor", [_row(Permission.CONVERSATIONS_READ_BRANCH, granted=True)]
    )
    assert Permission.CONVERSATIONS_READ_BRANCH in resolved
    assert Permission.CONVERSATIONS_CREATE in resolved  # defaults survive


def test_revoke_override_removes_a_default_permission():
    resolved = _resolve_with_rows(
        "doctor", [_row(Permission.CONVERSATIONS_CREATE, granted=False)]
    )
    assert Permission.CONVERSATIONS_CREATE not in resolved


def test_super_admin_bypasses_the_matrix_entirely():
    # Even a (hypothetical, schema-forbidden) revoke row must not reach super_admin.
    resolved = _resolve_with_rows(
        "super_admin", [_row(Permission.ADMIN_USERS_MANAGE, granted=False)]
    )
    assert resolved == ALL_PERMISSIONS


def test_unknown_permission_row_is_ignored_not_fatal():
    resolved = _resolve_with_rows(
        "doctor", [SimpleNamespace(permission="future:unknown", granted=True)]
    )
    assert resolved == DEFAULT_PERMISSIONS["doctor"]
