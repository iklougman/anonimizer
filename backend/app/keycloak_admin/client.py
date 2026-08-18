from __future__ import annotations

import time

import httpx

_TOKEN_REFRESH_SLACK_SECONDS = 30


class KeycloakAdminError(Exception):
    """The Keycloak Admin REST API call failed or returned an unexpected shape."""


class KeycloakAdminConflictError(KeycloakAdminError):
    """Keycloak rejected the request as a conflict (e.g. the email already exists)."""


class KeycloakAdminClient:
    """Thin wrapper over the subset of the Keycloak Admin REST API user
    provisioning needs. Optional: the admin API works without this configured
    (POST /api/admin/users falls back to "link an existing Keycloak subject"
    mode), so a missing/misconfigured admin client degrades a feature rather
    than blocking the app.
    """

    def __init__(
        self,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        self._http_client = http_client if http_client is not None else httpx.Client(timeout=10.0)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _access_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        try:
            response = self._http_client.post(
                f"{self._base_url}/realms/{self._realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(f"failed to obtain an admin token: {exc}") from exc

        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = (
            time.monotonic() + body["expires_in"] - _TOKEN_REFRESH_SLACK_SECONDS
        )
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def create_user(
        self, email: str, first_name: str, last_name: str, tenant_id: str
    ) -> str:
        """Creates a Keycloak user carrying the tenant_id attribute the JWT
        validator reads, and returns its subject (Keycloak user id)."""
        try:
            response = self._http_client.post(
                f"{self._base_url}/admin/realms/{self._realm}/users",
                headers=self._headers(),
                json={
                    "username": email,
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "enabled": True,
                    "emailVerified": False,
                    "requiredActions": ["UPDATE_PASSWORD"],
                    "attributes": {"tenant_id": [tenant_id]},
                },
            )
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(f"create_user request failed: {exc}") from exc

        if response.status_code == 409:
            raise KeycloakAdminConflictError(f"a Keycloak user for {email!r} already exists")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise KeycloakAdminError(f"create_user failed: {exc}") from exc

        location = response.headers.get("Location", "")
        subject = location.rsplit("/", 1)[-1]
        if not subject:
            raise KeycloakAdminError(f"create_user response had no usable Location header: {location!r}")
        return subject

    def set_enabled(self, subject: str, enabled: bool) -> None:
        try:
            response = self._http_client.put(
                f"{self._base_url}/admin/realms/{self._realm}/users/{subject}",
                headers=self._headers(),
                json={"enabled": enabled},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(f"set_enabled({subject!r}, {enabled}) failed: {exc}") from exc

    def send_invite(self, subject: str) -> None:
        try:
            response = self._http_client.put(
                f"{self._base_url}/admin/realms/{self._realm}/users/{subject}/execute-actions-email",
                headers=self._headers(),
                json=["UPDATE_PASSWORD"],
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Best-effort: dev/self-hosted Keycloak commonly has no SMTP
            # configured. The caller reports invite_email_sent=false rather
            # than failing the whole user-creation request over this.
            raise KeycloakAdminError(f"send_invite({subject!r}) failed: {exc}") from exc

    def delete_user(self, subject: str) -> None:
        """Compensation only -- called to undo a Keycloak create_user() when
        the follow-up DB write fails. Best-effort: swallows its own failure so
        a compensation attempt never masks the original error."""
        try:
            self._http_client.delete(
                f"{self._base_url}/admin/realms/{self._realm}/users/{subject}",
                headers=self._headers(),
            )
        except httpx.HTTPError:
            pass
