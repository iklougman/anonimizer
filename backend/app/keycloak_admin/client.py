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
        self,
        email: str,
        first_name: str,
        last_name: str,
        tenant_id: str,
        required_actions: list[str] | None = None,
    ) -> str:
        """Creates a Keycloak user carrying the tenant_id attribute the JWT
        validator reads, and returns its subject (Keycloak user id).

        `required_actions` defaults to UPDATE_PASSWORD (the admin-invite
        shape: no password set yet, Keycloak forces one on first login).
        The public self-signup flow passes ["VERIFY_EMAIL"] instead, since
        it sets a real password immediately via set_password()."""
        actions = required_actions if required_actions is not None else ["UPDATE_PASSWORD"]
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
                    "requiredActions": actions,
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

    def set_password(self, subject: str, password: str, temporary: bool = False) -> None:
        """Sets (resets) a user's password directly, clearing any pending
        UPDATE_PASSWORD required action when temporary=False. create_user()
        always sets that required action; a caller that needs the user to be
        immediately usable via a password grant (e.g. e2e tenant
        provisioning) must call this afterward."""
        try:
            response = self._http_client.put(
                f"{self._base_url}/admin/realms/{self._realm}/users/{subject}/reset-password",
                headers=self._headers(),
                json={"type": "password", "value": password, "temporary": temporary},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(f"set_password({subject!r}) failed: {exc}") from exc

    def send_required_actions_email(self, subject: str, actions: list[str]) -> None:
        try:
            response = self._http_client.put(
                f"{self._base_url}/admin/realms/{self._realm}/users/{subject}/execute-actions-email",
                headers=self._headers(),
                json=actions,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Best-effort: dev/self-hosted Keycloak commonly has no SMTP
            # configured. The caller reports the email as not-sent rather
            # than failing the whole request over this.
            raise KeycloakAdminError(f"send_required_actions_email({subject!r}, {actions}) failed: {exc}") from exc

    def send_invite(self, subject: str) -> None:
        self.send_required_actions_email(subject, ["UPDATE_PASSWORD"])

    def logout_user(self, subject: str) -> None:
        """Ends every active Keycloak SSO session for this user (defense in
        depth for account deactivation -- app-DB is_active is what actually
        makes deactivation take effect on the next request; this stops
        *future* refreshes/re-logins, not an already-issued access token
        before its own exp). Best-effort, same shape as set_enabled."""
        try:
            response = self._http_client.post(
                f"{self._base_url}/admin/realms/{self._realm}/users/{subject}/logout",
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(f"logout_user({subject!r}) failed: {exc}") from exc

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
