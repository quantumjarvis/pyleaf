"""@PreAuthorize/@PostAuthorize/@Secured + MethodSecurityInterceptor.

Secured beans run inside a real ``ApplicationContext`` with
``SecurityAutoConfiguration`` registered. Semantics pinned here:

* no usable authentication -> ``AuthenticationError`` (401 semantics)
* authenticated but the check fails -> ``AccessDeniedError`` (403 semantics)
* ``#param`` binds the method argument; ``#result``/``return_value`` binds
  the return value in post-authorize
* method-level annotation overrides class-level
* the aspect runs at @Order(50), OUTSIDE the tx interceptor (@Order(100)),
  so denied calls never open a transaction
"""

from __future__ import annotations

import importlib.metadata
from typing import Any

import pytest
from pyleaf.core import ApplicationContext, Component, Environment, PropertySource
from pyleaf.security import (
    AccessDeniedError,
    Authentication,
    AuthenticationError,
    MethodSecurityInterceptor,
    PostAuthorize,
    PreAuthorize,
    Principal,
    Secured,
    SecurityAutoConfiguration,
    SecurityContextHolder,
    SecurityExpressionError,
    role,
)


def _ctx(*classes: type, env: Environment | None = None) -> ApplicationContext:
    ctx = ApplicationContext(environment=env)
    for cls in classes:
        ctx.register(cls)
    ctx.register(SecurityAutoConfiguration)
    ctx.refresh()
    return ctx


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
def clean_context() -> Any:
    SecurityContextHolder.clear()
    yield
    SecurityContextHolder.clear()


# --- fixture beans ----------------------------------------------------------------


@Component
class VaultService:
    @Secured("ADMIN")
    def open(self) -> str:
        return "opened"

    @Secured("ADMIN", "USER")
    def peek(self) -> str:
        return "peeked"

    def unlocked(self) -> str:
        return "free"


@Component
class ProfileService:
    @PreAuthorize("has_role('ADMIN') and #name == authentication.principal.name")
    def rename(self, name: str) -> str:
        return f"hi {name}"

    @PreAuthorize("is_authenticated()")
    def touch(self) -> str:
        return "touched"


@Component
class DocService:
    @PostAuthorize("#result != 'forbidden'")
    def read(self, doc: str) -> str:
        return doc

    @PostAuthorize("return_value.owner == authentication.principal.name")
    def own(self, doc: Any) -> Any:
        return doc


@Component
class AsyncVault:
    @Secured("USER")
    async def work(self) -> str:
        return "done"

    @PreAuthorize("deny_all()")
    async def denied(self) -> str:
        return "never"


@Component
@PreAuthorize("deny_all()")
class GuardedService:
    def base(self) -> str:
        return "base"

    @PreAuthorize("permit_all()")
    def open(self) -> str:
        return "open"


class _Doc:
    def __init__(self, owner: str) -> None:
        self.owner = owner


# --- @Secured: 401 vs 403 ------------------------------------------------------------


def test_no_auth_raises_authentication_error() -> None:
    ctx = _ctx(VaultService)
    svc = ctx.get(VaultService)
    with pytest.raises(AuthenticationError):
        svc.open()


def test_wrong_role_raises_access_denied() -> None:
    ctx = _ctx(VaultService)
    svc = ctx.get(VaultService)
    with SecurityContextHolder.with_authentication(_auth(roles=("USER",))):
        with pytest.raises(AccessDeniedError):
            svc.open()


def test_correct_role_passes() -> None:
    ctx = _ctx(VaultService)
    svc = ctx.get(VaultService)
    with SecurityContextHolder.with_authentication(_auth(roles=("ADMIN",))):
        assert svc.open() == "opened"


def test_secured_roles_are_or_semantics() -> None:
    ctx = _ctx(VaultService)
    svc = ctx.get(VaultService)
    with SecurityContextHolder.with_authentication(_auth(roles=("USER",))):
        assert svc.peek() == "peeked"


def test_anonymous_auth_counts_as_no_auth() -> None:
    ctx = _ctx(VaultService)
    svc = ctx.get(VaultService)
    with SecurityContextHolder.with_authentication(
        _auth(roles=("ADMIN",), authenticated=False)
    ):
        with pytest.raises(AuthenticationError):
            svc.open()


def test_unsecured_method_passes_without_auth() -> None:
    ctx = _ctx(VaultService)
    assert ctx.get(VaultService).unlocked() == "free"


# --- @PreAuthorize ---------------------------------------------------------------------


def test_pre_authorize_param_equality_passes() -> None:
    ctx = _ctx(ProfileService)
    svc = ctx.get(ProfileService)
    with SecurityContextHolder.with_authentication(
        _auth("alice", roles=("ADMIN",))
    ):
        assert svc.rename("alice") == "hi alice"


def test_pre_authorize_param_mismatch_denies() -> None:
    ctx = _ctx(ProfileService)
    svc = ctx.get(ProfileService)
    with SecurityContextHolder.with_authentication(
        _auth("alice", roles=("ADMIN",))
    ):
        with pytest.raises(AccessDeniedError):
            svc.rename("bob")


def test_pre_authorize_no_auth_raises_authentication_error() -> None:
    ctx = _ctx(ProfileService)
    svc = ctx.get(ProfileService)
    with pytest.raises(AuthenticationError):
        svc.touch()


def test_is_authenticated_passes_for_any_authenticated_user() -> None:
    ctx = _ctx(ProfileService)
    svc = ctx.get(ProfileService)
    with SecurityContextHolder.with_authentication(_auth(roles=())):
        assert svc.touch() == "touched"


# --- @PostAuthorize ---------------------------------------------------------------------


def test_post_authorize_result_passes_and_denies() -> None:
    ctx = _ctx(DocService)
    svc = ctx.get(DocService)
    with SecurityContextHolder.with_authentication(_auth()):
        assert svc.read("ok") == "ok"
        with pytest.raises(AccessDeniedError):
            svc.read("forbidden")


def test_post_authorize_result_attribute_access() -> None:
    ctx = _ctx(DocService)
    svc = ctx.get(DocService)
    with SecurityContextHolder.with_authentication(_auth("alice")):
        assert svc.own(_Doc("alice")).owner == "alice"
        with pytest.raises(AccessDeniedError):
            svc.own(_Doc("mallory"))


def test_post_authorize_without_auth_raises_authentication_error() -> None:
    ctx = _ctx(DocService)
    svc = ctx.get(DocService)
    with pytest.raises(AuthenticationError):
        svc.read("forbidden")


# --- async methods -----------------------------------------------------------------------


async def test_async_secured_method() -> None:
    ctx = _ctx(AsyncVault)
    svc = ctx.get(AsyncVault)
    with SecurityContextHolder.with_authentication(_auth(roles=("USER",))):
        assert await svc.work() == "done"


async def test_async_no_auth_raises_authentication_error() -> None:
    ctx = _ctx(AsyncVault)
    svc = ctx.get(AsyncVault)
    with pytest.raises(AuthenticationError):
        await svc.work()


async def test_async_deny_all_is_access_denied_when_authenticated() -> None:
    ctx = _ctx(AsyncVault)
    svc = ctx.get(AsyncVault)
    with SecurityContextHolder.with_authentication(_auth(roles=("USER",))):
        with pytest.raises(AccessDeniedError):
            await svc.denied()


async def test_async_auth_does_not_leak_between_tasks() -> None:
    import asyncio

    ctx = _ctx(AsyncVault)
    svc = ctx.get(AsyncVault)

    async def alice() -> str:
        with SecurityContextHolder.with_authentication(_auth("alice")):
            await asyncio.sleep(0)
            return await svc.work()

    async def denied() -> type[BaseException] | None:
        await asyncio.sleep(0)
        try:
            await svc.work()
        except AuthenticationError as exc:
            return type(exc)
        return None

    a, d = await asyncio.gather(alice(), denied())
    assert a == "done"
    assert d is AuthenticationError


# --- class-level + override ---------------------------------------------------------------


def test_class_level_default_applies_to_unmarked_methods() -> None:
    ctx = _ctx(GuardedService)
    svc = ctx.get(GuardedService)
    with SecurityContextHolder.with_authentication(_auth(roles=("ADMIN",))):
        with pytest.raises(AccessDeniedError):
            svc.base()


def test_class_level_deny_all_without_auth_is_authentication_error() -> None:
    ctx = _ctx(GuardedService)
    svc = ctx.get(GuardedService)
    with pytest.raises(AuthenticationError):
        svc.base()


def test_method_level_overrides_class_level() -> None:
    ctx = _ctx(GuardedService)
    svc = ctx.get(GuardedService)
    # permit_all() permits even with no authentication in the context.
    assert svc.open() == "open"


# --- errors / refresh-time checks ------------------------------------------------------------


def test_unknown_param_reference_fails_at_decoration() -> None:
    with pytest.raises(SecurityExpressionError):

        @PreAuthorize("#missing == 'x'")
        def work(self: Any, present: str) -> str:
            return present


def test_class_level_bad_param_fails_at_refresh() -> None:
    @Component
    @PreAuthorize("#missing == 'x'")
    class BadService:
        def work(self, present: str) -> str:
            return present

    ctx = ApplicationContext()
    ctx.register(BadService)
    ctx.register(SecurityAutoConfiguration)
    with pytest.raises(SecurityExpressionError, match=r"BadService\.work"):
        ctx.refresh()


def test_secured_generator_fails_at_refresh() -> None:
    @Component
    class GenService:
        @Secured("ADMIN")
        def stream(self) -> Any:
            yield 1

    ctx = ApplicationContext()
    ctx.register(GenService)
    ctx.register(SecurityAutoConfiguration)
    with pytest.raises(TypeError, match=r"GenService\.stream"):
        ctx.refresh()


def test_secured_requires_at_least_one_role() -> None:
    with pytest.raises(SecurityExpressionError):
        Secured()


# --- interceptor wiring -----------------------------------------------------------------------


def test_autoconfigure_provides_the_interceptor_bean() -> None:
    ctx = _ctx(VaultService)
    aspect = ctx.get_bean("methodSecurityInterceptor")
    assert isinstance(aspect, MethodSecurityInterceptor)


def test_default_order_is_outside_the_tx_interceptor() -> None:
    # @Order(50) wraps targets outside the tx advice (@Order(100)): the
    # security check runs before any transaction can open.
    assert MethodSecurityInterceptor.__pyleaf_order__ == 50
    assert MethodSecurityInterceptor.__pyleaf_order__ < 100


def test_order_env_property_overrides_default() -> None:
    env = Environment(
        sources=[PropertySource("test", {"pyleaf.security.order": "5"})]
    )
    try:
        _ctx(VaultService, env=env)
        assert MethodSecurityInterceptor.__pyleaf_order__ == 5
    finally:
        MethodSecurityInterceptor.__pyleaf_order__ = 50


def test_entry_point_loads_security_autoconfigure() -> None:
    found = {
        ep.name: ep.load()
        for ep in importlib.metadata.entry_points(group="pyleaf.autoconfigure")
        if ep.name == "security"
    }
    assert found.get("security") is SecurityAutoConfiguration
