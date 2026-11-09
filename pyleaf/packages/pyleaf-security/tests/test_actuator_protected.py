"""Actuator default protection: ``/actuator/**`` requires ACTUATOR (WI 3.8).

The rule is appended AFTER user rules (user rules win) and removed by
``pyleaf.security.actuator.enabled=false`` — the paths then fall through
to ``pyleaf.security.default-access`` (pinned semantics).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import (
    SIGNING_KEY,
    ApiController,
    bearer,
    build_ctx,
    hs256_token,
)
from pyleaf.actuator.autoconfigure import ActuatorAutoConfiguration
from pyleaf.security.autoconfig import SecurityAutoConfiguration
from pyleaf.web.autoconfigure import WebAutoConfiguration

_BASE_VALUES = {"pyleaf.security.jwt.secret": SIGNING_KEY}


def _client(values: dict[str, object] | None = None) -> TestClient:
    ctx = build_ctx(
        ApiController,
        SecurityAutoConfiguration,
        WebAutoConfiguration,
        ActuatorAutoConfiguration,
        values={**_BASE_VALUES, **(values or {})},
    )
    return TestClient(ctx.get(FastAPI))


def test_actuator_health_anonymous_is_401() -> None:
    resp = _client().get("/actuator/health")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_actuator_health_with_actuator_role_is_200() -> None:
    token = hs256_token({"roles": ["ACTUATOR"]})
    resp = _client().get("/actuator/health", headers=bearer(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "UP"


def test_actuator_health_wrong_role_is_403() -> None:
    token = hs256_token({"roles": ["USER"]})
    resp = _client().get("/actuator/health", headers=bearer(token))
    assert resp.status_code == 403


def test_actuator_nested_paths_also_protected() -> None:
    resp = _client().get("/actuator/info")
    assert resp.status_code == 401


def test_actuator_security_disabled_falls_to_default_access() -> None:
    """enabled=false removes the rule; with permit_all anon passes."""
    client = _client(
        {
            "pyleaf.security.actuator.enabled": False,
            "pyleaf.security.default-access": "permit_all",
        }
    )
    resp = client.get("/actuator/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "UP"


def test_actuator_security_disabled_keeps_authenticated_default() -> None:
    """enabled=false without default-access: still 401, but any role works.

    With a USER-role JWT the request passes — proving the ACTUATOR-role
    rule is gone, not just shadowed.
    """
    client = _client({"pyleaf.security.actuator.enabled": False})
    assert client.get("/actuator/health").status_code == 401
    token = hs256_token({"roles": ["USER"]})
    assert client.get("/actuator/health", headers=bearer(token)).status_code == 200


def test_user_rules_take_precedence_over_actuator_default() -> None:
    """A user permit_all on an actuator path beats the built-in rule."""
    client = _client(
        {
            "pyleaf.security.rules": [
                {"path": "/actuator/health", "access": "permit_all"}
            ]
        }
    )
    assert client.get("/actuator/health").status_code == 200
    # Other actuator paths still hit the built-in ACTUATOR rule.
    assert client.get("/actuator/info").status_code == 401
