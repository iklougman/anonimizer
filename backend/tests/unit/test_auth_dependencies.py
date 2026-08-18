import uuid

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.dependencies import (
    get_current_user,
    get_jwt_validator,
    get_user_resolver,
    require_permission,
)
from app.auth.jwt_validator import InvalidTokenError, KeycloakUnreachableError, TokenClaims
from app.auth.permissions import DEFAULT_PERMISSIONS, Permission
from app.auth.tenant_resolver import AuthenticatedUser, InactiveUserError, UnknownTenantError


def _make_user(tenant_id=None, user_id=None, role="doctor", permissions=None):
    return AuthenticatedUser(
        tenant_id=tenant_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        role=role,
        branch_id=None,
        email="doc@example.com",
        permissions=DEFAULT_PERMISSIONS["doctor"] if permissions is None else permissions,
    )


class _FakeValidator:
    def __init__(self, result_or_error):
        self._result_or_error = result_or_error

    def validate(self, token):
        if isinstance(self._result_or_error, Exception):
            raise self._result_or_error
        return self._result_or_error


def _build_app(validator, resolver):
    app = FastAPI()
    app.dependency_overrides[get_jwt_validator] = lambda: validator
    app.dependency_overrides[get_user_resolver] = lambda: resolver

    @app.get("/whoami")
    def whoami(user: AuthenticatedUser = Depends(get_current_user)):
        return {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id)}

    return TestClient(app)


def test_missing_bearer_header_is_rejected():
    client = _build_app(_FakeValidator(Exception("unused")), resolver=lambda claims: None)
    response = client.get("/whoami")
    assert response.status_code == 401


def test_keycloak_unreachable_fails_closed_with_503():
    client = _build_app(
        _FakeValidator(KeycloakUnreachableError("jwks down")), resolver=lambda claims: None
    )
    response = client.get("/whoami", headers={"Authorization": "Bearer x"})
    assert response.status_code == 503


def test_invalid_token_is_rejected_with_401():
    client = _build_app(_FakeValidator(InvalidTokenError("bad sig")), resolver=lambda claims: None)
    response = client.get("/whoami", headers={"Authorization": "Bearer x"})
    assert response.status_code == 401


def test_unknown_tenant_is_rejected_with_401():
    claims = TokenClaims(tenant_id=uuid.uuid4(), keycloak_subject="sub-1")

    def resolver(_claims):
        raise UnknownTenantError("no such tenant")

    client = _build_app(_FakeValidator(claims), resolver=resolver)
    response = client.get("/whoami", headers={"Authorization": "Bearer x"})
    assert response.status_code == 401


def test_valid_token_resolves_to_the_authenticated_user():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    claims = TokenClaims(tenant_id=tenant_id, keycloak_subject="sub-1")

    def resolver(_claims):
        return _make_user(tenant_id=tenant_id, user_id=user_id)

    client = _build_app(_FakeValidator(claims), resolver=resolver)
    response = client.get("/whoami", headers={"Authorization": "Bearer x"})
    assert response.status_code == 200
    assert response.json() == {"tenant_id": str(tenant_id), "user_id": str(user_id)}


def test_inactive_user_is_rejected_with_403_not_401():
    claims = TokenClaims(tenant_id=uuid.uuid4(), keycloak_subject="sub-1")

    def resolver(_claims):
        raise InactiveUserError("deactivated")

    client = _build_app(_FakeValidator(claims), resolver=resolver)
    response = client.get("/whoami", headers={"Authorization": "Bearer x"})
    assert response.status_code == 403
    assert response.json()["detail"] == "account deactivated"


def _build_guarded_app(user):
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: user

    @app.get("/guarded")
    def guarded(
        _user: AuthenticatedUser = Depends(
            require_permission(Permission.ADMIN_USERS_MANAGE)
        ),
    ):
        return {"ok": True}

    return TestClient(app)


def test_require_permission_rejects_a_user_without_the_permission():
    client = _build_guarded_app(_make_user(permissions=DEFAULT_PERMISSIONS["doctor"]))
    response = client.get("/guarded", headers={"Authorization": "Bearer x"})
    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient permissions"


def test_require_permission_passes_a_user_holding_the_permission():
    client = _build_guarded_app(
        _make_user(permissions=frozenset({Permission.ADMIN_USERS_MANAGE}))
    )
    response = client.get("/guarded", headers={"Authorization": "Bearer x"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
