import httpx
import pytest

from app.keycloak_admin.client import (
    KeycloakAdminClient,
    KeycloakAdminConflictError,
    KeycloakAdminError,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "admin-tok", "expires_in": 300})


def test_create_user_sends_tenant_id_attribute_and_returns_subject_from_location():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/token"):
            return _token_response()
        assert request.headers["authorization"] == "Bearer admin-tok"
        import json

        body = json.loads(request.content)
        assert body["attributes"]["tenant_id"] == ["tenant-1"]
        assert body["requiredActions"] == ["UPDATE_PASSWORD"]
        return httpx.Response(
            201,
            headers={"Location": "http://keycloak/admin/realms/dev/users/subject-abc"},
        )

    admin = KeycloakAdminClient(
        base_url="http://keycloak",
        realm="dev",
        client_id="backend-admin",
        client_secret="s3cret",
        http_client=_client(handler),
    )

    subject = admin.create_user(
        email="doc@example.com", first_name="A", last_name="B", tenant_id="tenant-1"
    )

    assert subject == "subject-abc"


def test_token_is_cached_across_calls():
    token_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url.path.endswith("/token"):
            token_requests += 1
            return _token_response()
        return httpx.Response(200, json={})

    admin = KeycloakAdminClient(
        base_url="http://keycloak",
        realm="dev",
        client_id="backend-admin",
        client_secret="s3cret",
        http_client=_client(handler),
    )

    admin.set_enabled("subject-abc", True)
    admin.set_enabled("subject-abc", False)

    assert token_requests == 1


def test_create_user_conflict_raises_conflict_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return _token_response()
        return httpx.Response(409, json={"errorMessage": "User exists"})

    admin = KeycloakAdminClient(
        base_url="http://keycloak",
        realm="dev",
        client_id="backend-admin",
        client_secret="s3cret",
        http_client=_client(handler),
    )

    with pytest.raises(KeycloakAdminConflictError):
        admin.create_user(email="dup@example.com", first_name="", last_name="", tenant_id="t1")


def test_transport_error_raises_keycloak_admin_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    admin = KeycloakAdminClient(
        base_url="http://keycloak",
        realm="dev",
        client_id="backend-admin",
        client_secret="s3cret",
        http_client=_client(handler),
    )

    with pytest.raises(KeycloakAdminError):
        admin.set_enabled("subject-abc", False)


def test_delete_user_swallows_its_own_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return _token_response()
        raise httpx.ConnectError("connection refused", request=request)

    admin = KeycloakAdminClient(
        base_url="http://keycloak",
        realm="dev",
        client_id="backend-admin",
        client_secret="s3cret",
        http_client=_client(handler),
    )

    admin.delete_user("subject-abc")  # must not raise
