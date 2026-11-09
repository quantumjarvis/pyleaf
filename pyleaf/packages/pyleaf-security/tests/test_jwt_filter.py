"""JWT bearer authentication through the SecurityMiddleware (WI 3.8).

Every case runs a real FastAPI app (WebAutoConfiguration + the security
beans) over ``TestClient``. Default access is ``authenticated``, so
``/api/whoami`` is the protected probe path.
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import (
    SIGNING_KEY,
    WRONG_KEY,
    ApiController,
    bearer,
    build_ctx,
    hs256_token,
)
from pyleaf.security import (
    AuthenticationError,
    SecurityContextHolder,
    authentication_from_claims,
)
from pyleaf.security.autoconfig import SecurityAutoConfiguration
from pyleaf.security.jwt import JwtAuthenticationProvider
from pyleaf.web.autoconfigure import WebAutoConfiguration


def _client(values: dict[str, object] | None = None) -> TestClient:
    ctx = build_ctx(
        ApiController,
        SecurityAutoConfiguration,
        WebAutoConfiguration,
        values={"pyleaf.security.jwt.secret": SIGNING_KEY, **(values or {})},
    )
    return TestClient(ctx.get(FastAPI))


def test_valid_token_sets_principal_and_roles_in_handler() -> None:
    """The contextvar propagates through call_next into the handler."""
    token = hs256_token({"roles": ["USER", "ADMIN"], "scope": "read write"})
    resp = _client().get("/api/whoami", headers=bearer(token))

    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["name"] == "alice"
    assert "ROLE_USER" in body["authorities"]
    assert "ROLE_ADMIN" in body["authorities"]
    assert "SCOPE_read" in body["authorities"]


def test_missing_authorization_header_is_anonymous_401() -> None:
    resp = _client().get("/api/whoami")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Authentication required"}
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_expired_token_is_401() -> None:
    token = hs256_token({"exp": int(time.time()) - 30})
    resp = _client().get("/api/whoami", headers=bearer(token))
    assert resp.status_code == 401


def test_bad_signature_is_401() -> None:
    token = hs256_token(key=WRONG_KEY)
    resp = _client().get("/api/whoami", headers=bearer(token))
    assert resp.status_code == 401


def test_missing_exp_claim_is_401() -> None:
    token = str(
        pyjwt.encode({"sub": "alice"}, SIGNING_KEY, algorithm="HS256")
    )
    resp = _client().get("/api/whoami", headers=bearer(token))
    assert resp.status_code == 401


def test_malformed_token_is_401_not_500() -> None:
    resp = _client().get("/api/whoami", headers=bearer("not.a.jwt"))
    assert resp.status_code == 401


def test_wrong_algorithm_is_401() -> None:
    # An RS256-shaped token against an HS256 provider must not decode.
    token = str(
        pyjwt.encode(
            {"sub": "alice", "exp": int(time.time()) + 3600},
            SIGNING_KEY,
            algorithm="HS384",
        )
    )
    resp = _client().get("/api/whoami", headers=bearer(token))
    assert resp.status_code == 401


def test_wrong_audience_is_401() -> None:
    client = _client({"pyleaf.security.jwt.audience": "pyleaf-api"})
    token = hs256_token({"aud": "someone-else"})
    resp = client.get("/api/whoami", headers=bearer(token))
    assert resp.status_code == 401


def test_configured_issuer_and_audience_accepted() -> None:
    client = _client(
        {
            "pyleaf.security.jwt.issuer": "https://auth.example",
            "pyleaf.security.jwt.audience": "pyleaf-api",
        }
    )
    token = hs256_token(
        {"iss": "https://auth.example", "aud": "pyleaf-api"}
    )
    resp = client.get("/api/whoami", headers=bearer(token))
    assert resp.status_code == 200


def test_wrong_issuer_is_401() -> None:
    client = _client({"pyleaf.security.jwt.issuer": "https://auth.example"})
    token = hs256_token({"iss": "https://evil.example"})
    resp = client.get("/api/whoami", headers=bearer(token))
    assert resp.status_code == 401


def test_non_bearer_scheme_treated_as_anonymous() -> None:
    resp = _client().get(
        "/api/whoami", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert resp.status_code == 401


def test_context_cleared_between_requests() -> None:
    """No auth leakage: a second anonymous request sees no stale token."""
    client = _client(
        {"pyleaf.security.default-access": "permit_all"}
    )
    first = client.get("/api/whoami", headers=bearer(hs256_token()))
    assert first.status_code == 200
    assert first.json()["name"] == "alice"

    second = client.get("/api/whoami")
    assert second.status_code == 200
    body = second.json()
    assert body["authenticated"] is False
    assert body["name"] == "anonymousUser"
    assert SecurityContextHolder.get_authentication() is None


def test_rs256_public_key_verification() -> None:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    provider = JwtAuthenticationProvider(public_key=public_pem)
    token = str(
        pyjwt.encode(
            {"sub": "bob", "exp": int(time.time()) + 3600, "roles": ["OPS"]},
            private_pem,
            algorithm="RS256",
        )
    )
    auth = provider.authenticate(token)
    assert auth is not None
    assert auth.principal.name == "bob"
    assert auth.has_role("OPS")

    # A token signed by a DIFFERENT key fails signature verification.
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = str(
        pyjwt.encode(
            {"sub": "bob", "exp": int(time.time()) + 3600},
            other_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            algorithm="RS256",
        )
    )
    with pytest.raises(AuthenticationError):
        provider.authenticate(forged)


def test_claims_mapping_documented_shape() -> None:
    """Direct claims->Authentication mapping: roles/authorities/scope."""
    auth = authentication_from_claims(
        {
            "sub": "carol",
            "roles": ["ADMIN", "ROLE_OPS"],
            "authorities": ["feature:flag"],
            "scope": "read write",
            "scp": ["extra"],
        }
    )
    assert auth.principal.name == "carol"
    assert auth.has_role("ADMIN")
    assert auth.has_role("OPS")  # ROLE_ prefix is idempotent
    assert auth.has_authority("feature:flag")
    assert auth.has_authority("SCOPE_read")
    assert auth.has_authority("SCOPE_extra")
    # sub fallback chain: preferred_username -> "jwt-user"
    assert (
        authentication_from_claims({"preferred_username": "dave"})
        .principal.name
        == "dave"
    )
    assert authentication_from_claims({}).principal.name == "jwt-user"


def test_hs256_decode_and_context_set_under_100us() -> None:
    """Perf pin (same-run measured): median decode+context < 100 µs."""
    provider = JwtAuthenticationProvider(secret=SIGNING_KEY)
    token = hs256_token()
    provider.authenticate(token)  # warm the verify path
    samples: list[int] = []
    for _ in range(300):
        start = time.perf_counter_ns()
        auth = provider.authenticate(token)
        reset = SecurityContextHolder.set_authentication(auth)
        SecurityContextHolder.reset(reset)
        samples.append(time.perf_counter_ns() - start)
    samples.sort()
    median_ns = samples[len(samples) // 2]
    assert median_ns < 100_000, (
        f"HS256 decode+context median {median_ns}ns exceeds the 100us pin"
    )
