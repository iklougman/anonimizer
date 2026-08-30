from __future__ import annotations

import time
import uuid

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

# Keycloak rotates signing keys rarely; caching the JWKS response avoids putting a
# network round-trip (and a Keycloak outage) on every single authenticated request.
_JWKS_CACHE_TTL_SECONDS = 300

# When a token's kid isn't found in the cached JWKS, we force one fresh fetch so a
# genuine key rotation doesn't reject valid tokens for up to _JWKS_CACHE_TTL_SECONDS.
# This cooldown bounds how often that forced fetch can happen, since the trigger
# (an unrecognized kid) is attacker-controlled and could otherwise be used to flood
# Keycloak with JWKS requests.
_FORCED_REFRESH_COOLDOWN_SECONDS = 5


class KeycloakUnreachableError(Exception):
    """The JWKS endpoint could not be reached. ADR-0020/0021: fail closed, no
    fallback authentication path."""


class InvalidTokenError(Exception):
    """The token's signature, issuer, audience, expiry, or required claims failed
    verification."""


class TokenClaims:
    __slots__ = ("tenant_id", "keycloak_subject")

    def __init__(self, tenant_id: uuid.UUID, keycloak_subject: str) -> None:
        self.tenant_id = tenant_id
        self.keycloak_subject = keycloak_subject


class JWTValidator:
    """Validates a Keycloak-issued JWT against its realm's JWKS (RS256 only)."""

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._http_client = http_client if http_client is not None else httpx.Client(timeout=5.0)
        self._jwks_cache: dict[str, object] | None = None
        self._jwks_cached_at: float = 0.0
        self._last_forced_refresh_at: float = 0.0

    def validate(self, token: str) -> TokenClaims:
        jwks = self._get_jwks()

        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError(f"malformed token: {exc}") from exc

        key = next((k for k in jwks["keys"] if k.get("kid") == header.get("kid")), None)
        if key is None:
            # The kid isn't in our cached JWKS. This could be a genuine key rotation
            # on Keycloak's side, so force one fresh fetch before giving up -- but only
            # if we haven't already forced a refresh recently, since an attacker can
            # trivially trigger this path by sending a bogus kid.
            now = time.monotonic()
            if now - self._last_forced_refresh_at >= _FORCED_REFRESH_COOLDOWN_SECONDS:
                self._last_forced_refresh_at = now
                jwks = self._get_jwks(force_refresh=True)
                key = next((k for k in jwks["keys"] if k.get("kid") == header.get("kid")), None)
        if key is None:
            raise InvalidTokenError(f"no signing key found for kid={header.get('kid')!r}")
        public_key = RSAAlgorithm.from_jwk(key)

        try:
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError(str(exc)) from exc

        tenant_id_claim = claims.get("tenant_id")
        keycloak_subject = claims.get("sub")
        if not tenant_id_claim or not keycloak_subject:
            raise InvalidTokenError("token is missing required tenant_id or sub claim")
        try:
            tenant_id = uuid.UUID(tenant_id_claim)
        except ValueError as exc:
            raise InvalidTokenError(
                f"tenant_id claim is not a valid UUID: {tenant_id_claim!r}"
            ) from exc

        return TokenClaims(tenant_id=tenant_id, keycloak_subject=keycloak_subject)

    def _get_jwks(self, force_refresh: bool = False) -> dict[str, object]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._jwks_cache is not None
            and now - self._jwks_cached_at < _JWKS_CACHE_TTL_SECONDS
        ):
            return self._jwks_cache
        try:
            response = self._http_client.get(self._jwks_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KeycloakUnreachableError(
                f"could not fetch JWKS from {self._jwks_url}: {exc}"
            ) from exc
        self._jwks_cache = response.json()
        self._jwks_cached_at = now
        return self._jwks_cache
