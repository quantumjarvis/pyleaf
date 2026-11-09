"""SecurityContextHolder: contextvar-backed authentication state.

Pinned semantics: per-task isolation (asyncio tasks copy the context at
creation), token-based set/restore via ``with_authentication``, and the
401-typed ``require_authentication`` guard for absent or anonymous auth.
"""

from __future__ import annotations

import asyncio
import contextvars

import pytest
from pyleaf.security import (
    Authentication,
    AuthenticationError,
    GrantedAuthority,
    Principal,
    SecurityContextHolder,
    role,
)


def _auth(
    name: str = "alice",
    roles: tuple[str, ...] = ("USER",),
    authenticated: bool = True,
) -> Authentication:
    return Authentication(
        principal=Principal(name),
        authorities=frozenset(role(r) for r in roles),
        authenticated=authenticated,
    )


@pytest.fixture(autouse=True)
def clean_context() -> object:
    SecurityContextHolder.clear()
    yield
    SecurityContextHolder.clear()


# --- domain types -----------------------------------------------------------------


def test_role_helper_applies_prefix_idempotently() -> None:
    assert role("ADMIN") == GrantedAuthority("ROLE_ADMIN")
    assert role("ROLE_ADMIN") == GrantedAuthority("ROLE_ADMIN")


def test_has_role_and_has_authority() -> None:
    auth = _auth(roles=("ADMIN",))
    assert auth.has_role("ADMIN")
    assert auth.has_role("ROLE_ADMIN")
    assert not auth.has_role("USER")
    assert auth.has_authority("ROLE_ADMIN")
    assert not auth.has_authority("ROLE_USER")


def test_credentials_are_never_stored() -> None:
    auth = _auth()
    assert auth.credentials is None


def test_principal_carries_name_and_details() -> None:
    principal = Principal("bob", details={"tenant": "t1"})
    assert principal.name == "bob"
    assert principal.details == {"tenant": "t1"}


# --- get / set / clear ---------------------------------------------------------------


def test_get_returns_none_when_unset() -> None:
    assert SecurityContextHolder.get_authentication() is None


def test_set_get_clear_roundtrip() -> None:
    auth = _auth()
    SecurityContextHolder.set_authentication(auth)
    assert SecurityContextHolder.get_authentication() is auth
    SecurityContextHolder.clear()
    assert SecurityContextHolder.get_authentication() is None


def test_clear_is_idempotent() -> None:
    SecurityContextHolder.clear()
    SecurityContextHolder.clear()
    assert SecurityContextHolder.get_authentication() is None


def test_require_returns_auth_when_present() -> None:
    auth = _auth()
    SecurityContextHolder.set_authentication(auth)
    assert SecurityContextHolder.require_authentication() is auth


def test_require_raises_authentication_error_when_absent() -> None:
    with pytest.raises(AuthenticationError):
        SecurityContextHolder.require_authentication()


def test_require_raises_for_anonymous_auth() -> None:
    SecurityContextHolder.set_authentication(_auth(authenticated=False))
    with pytest.raises(AuthenticationError):
        SecurityContextHolder.require_authentication()


# --- with_authentication ---------------------------------------------------------------


def test_with_authentication_sets_and_restores() -> None:
    outer = _auth("outer")
    inner = _auth("inner")
    SecurityContextHolder.set_authentication(outer)
    with SecurityContextHolder.with_authentication(inner):
        assert SecurityContextHolder.get_authentication() is inner
    assert SecurityContextHolder.get_authentication() is outer


def test_with_authentication_restores_none() -> None:
    with SecurityContextHolder.with_authentication(_auth()):
        assert SecurityContextHolder.get_authentication() is not None
    assert SecurityContextHolder.get_authentication() is None


def test_with_authentication_restores_on_exception() -> None:
    outer = _auth("outer")
    SecurityContextHolder.set_authentication(outer)
    with (
        pytest.raises(RuntimeError),
        SecurityContextHolder.with_authentication(_auth("inner")),
    ):
        raise RuntimeError("boom")
    assert SecurityContextHolder.get_authentication() is outer


def test_with_authentication_nested() -> None:
    a, b, c = _auth("a"), _auth("b"), _auth("c")
    with SecurityContextHolder.with_authentication(a):
        with SecurityContextHolder.with_authentication(b):
            with SecurityContextHolder.with_authentication(c):
                assert SecurityContextHolder.get_authentication() is c
            assert SecurityContextHolder.get_authentication() is b
        assert SecurityContextHolder.get_authentication() is a


def test_with_authentication_as_decorator() -> None:
    auth = _auth("svc")

    @SecurityContextHolder.with_authentication(auth)
    def whoami() -> str:
        return SecurityContextHolder.require_authentication().principal.name

    assert whoami() == "svc"
    # Reusable: a second call gets a fresh token, not a stale context.
    assert whoami() == "svc"
    assert SecurityContextHolder.get_authentication() is None


# --- isolation ---------------------------------------------------------------------------


async def test_concurrent_tasks_do_not_leak_authentication() -> None:
    SecurityContextHolder.set_authentication(_auth("parent"))
    barrier = asyncio.Event()
    seen: dict[str, str | None] = {}

    async def worker(name: str) -> None:
        SecurityContextHolder.set_authentication(_auth(name))
        # Yield so both tasks interleave inside their own context copies.
        await barrier.wait()
        auth = SecurityContextHolder.get_authentication()
        seen[name] = auth.principal.name if auth is not None else None

    t1 = asyncio.create_task(worker("task-one"))
    t2 = asyncio.create_task(worker("task-two"))
    await asyncio.sleep(0)
    barrier.set()
    await asyncio.gather(t1, t2)

    assert seen == {"task-one": "task-one", "task-two": "task-two"}
    parent = SecurityContextHolder.get_authentication()
    assert parent is not None and parent.principal.name == "parent"


def test_copied_context_does_not_leak_back() -> None:
    SecurityContextHolder.set_authentication(_auth("outer"))
    copied = contextvars.copy_context()
    copied.run(SecurityContextHolder.set_authentication, _auth("copied"))
    auth = SecurityContextHolder.get_authentication()
    assert auth is not None and auth.principal.name == "outer"


def test_child_context_sees_parent_auth_at_copy_time() -> None:
    SecurityContextHolder.set_authentication(_auth("outer"))
    copied = contextvars.copy_context()
    auth = copied.run(SecurityContextHolder.get_authentication)
    assert auth is not None and auth.principal.name == "outer"
