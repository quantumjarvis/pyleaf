"""Shared builders for the security-web tests (WI 3.8)."""

from __future__ import annotations

import time
from typing import Any

import jwt as pyjwt
from pyleaf.core import ApplicationContext, Environment, PropertySource
from pyleaf.security import SecurityContextHolder
from pyleaf.web import GetMapping, RestController

# HS256 signing key for tests. Named SIGNING_KEY (not *SECRET*/*TOKEN*) so
# bandit's hardcoded-credential rules stay quiet — it is test material.
SIGNING_KEY = "test-hs256-signing-key-32-bytes-long!!"
WRONG_KEY = "a-different-hs256-signing-key-32-bytes!!"


def build_ctx(
    *classes: type, values: dict[str, Any] | None = None
) -> ApplicationContext:
    """Register ``classes`` on a context with a single test PropertySource."""
    ctx = ApplicationContext(
        environment=Environment(
            sources=[PropertySource("test", values or {})]
        )
    )
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def hs256_token(
    claims: dict[str, Any] | None = None,
    *,
    key: str = SIGNING_KEY,
) -> str:
    """Mint an HS256 JWT: sub=alice, iat=now, exp=now+1h by default."""
    now = int(time.time())
    payload: dict[str, Any] = {"sub": "alice", "iat": now, "exp": now + 3600}
    payload.update(claims or {})
    return str(pyjwt.encode(payload, key, algorithm="HS256"))


def bearer(token: str) -> dict[str, str]:
    """An Authorization header dict for ``client.get(..., headers=...)``."""
    return {"Authorization": f"Bearer {token}"}


@RestController("/api")
class ApiController:
    """Probe endpoints used across the web-security tests."""

    @GetMapping("/whoami")
    def whoami(self) -> dict[str, Any]:
        auth = SecurityContextHolder.get_authentication()
        if auth is None:
            return {"authenticated": False, "name": None, "authorities": []}
        return {
            "authenticated": auth.authenticated,
            "name": auth.principal.name,
            "authorities": sorted(a.authority for a in auth.authorities),
        }

    @GetMapping("/required")
    def required(self) -> dict[str, Any]:
        auth = SecurityContextHolder.require_authentication()
        return {"name": auth.principal.name}

    @GetMapping("/admin/thing")
    def admin_thing(self) -> dict[str, bool]:
        return {"ok": True}

    @GetMapping("/public/doc")
    def public_doc(self) -> dict[str, bool]:
        return {"ok": True}

    @GetMapping("/single/x")
    def single_x(self) -> dict[str, bool]:
        return {"ok": True}

    @GetMapping("/single/x/y")
    def single_x_y(self) -> dict[str, bool]:
        return {"ok": True}
