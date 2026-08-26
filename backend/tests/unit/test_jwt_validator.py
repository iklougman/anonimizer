import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.auth.jwt_validator import InvalidTokenError, JWTValidator, KeycloakUnreachableError

ISSUER = "http://localhost:8080/realms/chatgpt-proxy-dev"
AUDIENCE = "chatgpt-proxy-frontend"
JWKS_URL = "http://keycloak:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/certs"
KID = "test-key-1"


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def jwks_payload(rsa_keypair):
    _, public_key = rsa_keypair
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = KID
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


@pytest.fixture
def mock_http():
    """A stand-in http_client whose .get() is a MagicMock, so tests can queue up a
    distinct response per call via side_effect and assert on call_count -- unlike
    the module-level httpx.MockTransport handler above, which always serves the
    same fixed payload."""
    return MagicMock()


@pytest.fixture
def validator(mock_http) -> JWTValidator:
    return JWTValidator(jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, http_client=mock_http)


def _jwks_response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("GET", JWKS_URL))


def _jwk_for(public_key, kid: str) -> dict:
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


def _make_validator(jwks_payload) -> JWTValidator:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return JWTValidator(jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, http_client=client)


def _make_token(
    private_key,
    *,
    tenant_id=None,
    sub="doctor-1",
    issuer=ISSUER,
    audience=AUDIENCE,
    expired=False,
    kid=KID,
):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "iat": now,
        "exp": now - timedelta(minutes=5) if expired else now + timedelta(minutes=5),
    }
    if tenant_id is not None:
        claims["tenant_id"] = str(tenant_id)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def test_valid_token_returns_claims(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    tenant_id = uuid.uuid4()
    token = _make_token(private_key, tenant_id=tenant_id)

    claims = validator.validate(token)

    assert claims.tenant_id == tenant_id
    assert claims.keycloak_subject == "doctor-1"


def test_expired_token_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=uuid.uuid4(), expired=True)

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_wrong_issuer_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=uuid.uuid4(), issuer="http://evil.example/realms/x")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_wrong_audience_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=uuid.uuid4(), audience="some-other-client")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_missing_tenant_id_claim_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=None)

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_unknown_kid_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=uuid.uuid4(), kid="not-in-jwks")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_signature_from_a_different_key_is_rejected(jwks_payload):
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    validator = _make_validator(jwks_payload)
    token = _make_token(other_private_key, tenant_id=uuid.uuid4())

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_jwks_endpoint_unreachable_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = JWTValidator(jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, http_client=client)

    with pytest.raises(KeycloakUnreachableError):
        validator.validate("irrelevant-token")


def test_kid_miss_forces_a_fresh_jwks_fetch_before_failing(rsa_keypair, jwks_payload, validator, mock_http):
    # First fetch returns a JWKS with only the old kid; second (forced) fetch
    # returns one with the new kid too -- simulating a real key rotation.
    new_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rotated_jwks_response = {
        "keys": jwks_payload["keys"] + [_jwk_for(new_private_key.public_key(), "new-kid")]
    }
    mock_http.get.side_effect = [
        _jwks_response(jwks_payload),
        _jwks_response(rotated_jwks_response),
    ]
    tenant_id = uuid.uuid4()
    token = _make_token(new_private_key, tenant_id=tenant_id, kid="new-kid")

    claims = validator.validate(token)

    assert mock_http.get.call_count == 2
    assert claims.tenant_id == tenant_id


def test_kid_still_missing_after_forced_refresh_raises_invalid_token(rsa_keypair, jwks_payload, validator, mock_http):
    private_key, _ = rsa_keypair
    mock_http.get.side_effect = [_jwks_response(jwks_payload), _jwks_response(jwks_payload)]
    token = _make_token(private_key, tenant_id=uuid.uuid4(), kid="never-existed")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)

    assert mock_http.get.call_count == 2


def test_forced_refresh_has_a_cooldown(rsa_keypair, jwks_payload, validator, mock_http):
    private_key, _ = rsa_keypair
    mock_http.get.side_effect = [_jwks_response(jwks_payload)] * 4
    token = _make_token(private_key, tenant_id=uuid.uuid4(), kid="missing")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)
    with pytest.raises(InvalidTokenError):
        validator.validate(token)  # second call, still within the cooldown window

    # Only ONE forced refresh happened across both calls (2 total fetches: the
    # initial cache-miss fetch, plus one forced refresh) -- the second validate()
    # call's kid-miss did not trigger a second forced fetch.
    assert mock_http.get.call_count == 2
