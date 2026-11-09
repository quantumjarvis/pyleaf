"""Ordered Ant-style path rules + default-access (WI 3.8).

First-match precedence, ``**``/``*`` globs, permit_all/authenticated/
roles/deny_all decisions, 401 vs 403 and the ``default-access``
property. The actuator default rule is covered by
``test_actuator_protected.py``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import (
    SIGNING_KEY,
    ApiController,
    bearer,
    build_ctx,
    hs256_token,
)
from pyleaf.core.errors import PyleafError
from pyleaf.security import PathRule, parse_rules
from pyleaf.security.autoconfig import SecurityAutoConfiguration
from pyleaf.web.autoconfigure import WebAutoConfiguration

_BASE_VALUES = {"pyleaf.security.jwt.secret": SIGNING_KEY}


def _client(values: dict[str, object]) -> TestClient:
    ctx = build_ctx(
        ApiController,
        SecurityAutoConfiguration,
        WebAutoConfiguration,
        values={**_BASE_VALUES, **values},
    )
    return TestClient(ctx.get(FastAPI))


def test_permit_all_path_allows_anonymous() -> None:
    client = _client(
        {
            "pyleaf.security.rules": [
                {"path": "/api/public/**", "access": "permit_all"}
            ]
        }
    )
    assert client.get("/api/public/doc").status_code == 200


def test_authenticated_path_rejects_anonymous_with_401() -> None:
    client = _client({"pyleaf.security.rules": []})
    resp = client.get("/api/whoami")
    assert resp.status_code == 401


def test_role_path_wrong_role_is_403() -> None:
    client = _client(
        {
            "pyleaf.security.rules": [
                {"path": "/api/admin/**", "roles": ["ADMIN"]}
            ]
        }
    )
    resp = client.get(
        "/api/admin/thing", headers=bearer(hs256_token({"roles": ["USER"]}))
    )
    assert resp.status_code == 403


def test_role_path_anonymous_is_401_not_403() -> None:
    client = _client(
        {
            "pyleaf.security.rules": [
                {"path": "/api/admin/**", "roles": ["ADMIN"]}
            ]
        }
    )
    assert client.get("/api/admin/thing").status_code == 401


def test_role_path_matching_role_is_200() -> None:
    client = _client(
        {
            "pyleaf.security.rules": [
                {"path": "/api/admin/**", "roles": ["ADMIN"]}
            ]
        }
    )
    resp = client.get(
        "/api/admin/thing",
        headers=bearer(hs256_token({"roles": ["ROLE_ADMIN"]})),
    )
    assert resp.status_code == 200


def test_first_match_wins_not_most_specific() -> None:
    """A broad permit_all declared FIRST shadows a later roles rule."""
    client = _client(
        {
            "pyleaf.security.rules": [
                {"path": "/api/**", "access": "permit_all"},
                {"path": "/api/admin/**", "roles": ["ADMIN"]},
            ]
        }
    )
    # First match wins: /api/admin/thing hits the permit_all rule.
    assert client.get("/api/admin/thing").status_code == 200


def test_single_star_stays_within_one_segment() -> None:
    client = _client(
        {
            "pyleaf.security.default-access": "permit_all",
            "pyleaf.security.rules": [
                {"path": "/api/single/*", "roles": ["ADMIN"]}
            ],
        }
    )
    # One segment: roles rule matches -> USER is 403.
    assert (
        client.get(
            "/api/single/x",
            headers=bearer(hs256_token({"roles": ["USER"]})),
        ).status_code
        == 403
    )
    # Two segments: '*' does not match '/y' -> falls to permit_all.
    assert client.get("/api/single/x/y").status_code == 200


def test_double_star_matches_nested_and_bare_prefix() -> None:
    rule = PathRule("/api/admin/**", "permit_all")
    assert rule.matches("/api/admin")
    assert rule.matches("/api/admin/")
    assert rule.matches("/api/admin/thing")
    assert rule.matches("/api/admin/a/b/c")
    assert not rule.matches("/api/administer")


def test_deny_all_blocks_everyone() -> None:
    client = _client(
        {
            "pyleaf.security.rules": [
                {"path": "/api/admin/**", "access": "deny_all"}
            ]
        }
    )
    assert (
        client.get(
            "/api/admin/thing",
            headers=bearer(hs256_token({"roles": ["ADMIN"]})),
        ).status_code
        == 403
    )


def test_default_access_permit_all() -> None:
    client = _client({"pyleaf.security.default-access": "permit_all"})
    resp = client.get("/api/whoami")
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False


def test_default_access_defaults_to_authenticated() -> None:
    client = _client({})
    assert client.get("/api/whoami").status_code == 401


def test_invalid_default_access_fails_fast() -> None:
    with pytest.raises(PyleafError):
        _client({"pyleaf.security.default-access": "sometimes"})


def test_rule_path_only_means_authenticated() -> None:
    rules = parse_rules([{"path": "/api/**"}])
    assert rules[0].access == "authenticated"


def test_rule_roles_and_access_together_is_rejected() -> None:
    with pytest.raises(PyleafError):
        parse_rules(
            [{"path": "/x", "roles": ["A"], "access": "permit_all"}]
        )


def test_rule_missing_path_is_rejected() -> None:
    with pytest.raises(PyleafError):
        parse_rules([{"access": "permit_all"}])


def test_rule_unknown_access_is_rejected() -> None:
    with pytest.raises(PyleafError):
        parse_rules([{"path": "/x", "access": "maybe"}])


def test_roles_claim_without_prefix_matches() -> None:
    """Rules normalize 'ADMIN' and 'ROLE_ADMIN' identically."""
    client = _client(
        {
            "pyleaf.security.rules": [
                {"path": "/api/admin/**", "roles": "ROLE_ADMIN"}
            ]
        }
    )
    resp = client.get(
        "/api/admin/thing", headers=bearer(hs256_token({"roles": ["ADMIN"]}))
    )
    assert resp.status_code == 200
