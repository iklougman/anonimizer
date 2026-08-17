import json
import uuid
from datetime import datetime, timedelta, timezone

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
