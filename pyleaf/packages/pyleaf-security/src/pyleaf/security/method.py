"""``@PreAuthorize``/``@PostAuthorize``/``@Secured`` + the AOP interceptor.

Mirrors :mod:`pyleaf.tx.interceptor`: the markers record metadata under
``__pyleaf_security_*__`` attributes (method-level marks override
class-level ones, resolved independently per marker kind), the pointcut
predicate performs the bean-creation checks at ``refresh()`` — generator
rejection and expression-parameter validation — and the aspect carries
dual sync/async ``@Around`` advices.

``@Order(50)`` places the security advice OUTSIDE the tx interceptor
(``@Order(100)``): lower order wraps further out, so the check runs
before the transaction boundary opens and a denied call never begins a
transaction.

Deny semantics: a falsy check raises ``AuthenticationError`` (401) when
the context has no usable authentication — absent or anonymous — and
``AccessDeniedError`` (403) otherwise.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pyleaf.core import (
    Around,
    Aspect,
    Invocation,
    Order,
    Pointcut,
)

from .context import SecurityContextHolder
from .core import Authentication
from .errors import AccessDeniedError, AuthenticationError
from .expression import ParsedExpression, SecurityExpressionError

F = TypeVar("F", bound=Callable[..., Any])

#: Attributes under which the markers store their (already parsed) meta.
PRE_ATTR = "__pyleaf_security_pre__"
POST_ATTR = "__pyleaf_security_post__"
SECURED_ATTR = "__pyleaf_secured_roles__"

#: Default order of the security advice — outside tx's 100.
DEFAULT_SECURITY_ORDER = 50

#: Environment property overriding the advice order.
SECURITY_ORDER_PROPERTY = "pyleaf.security.order"


@dataclass(frozen=True)
class SecurityMeta:
    """Effective security metadata for one method, marks already merged."""

    pre: ParsedExpression | None = None
    post: ParsedExpression | None = None
    roles: tuple[str, ...] | None = None


def security_meta(
    bean_cls: type[Any], func: Callable[..., Any] | None
) -> SecurityMeta | None:
    """Resolve the effective security metadata for a method.

    Each marker kind resolves independently — a method-level mark
    overrides the (possibly inherited) class-level mark of the same
    kind; marks of different kinds combine. ``func`` may be ``None``
    for non-function attributes, falling back to class-level marks.
    """
    pre = getattr(func, PRE_ATTR, None) if func is not None else None
    if pre is None:
        pre = getattr(bean_cls, PRE_ATTR, None)
    post = getattr(func, POST_ATTR, None) if func is not None else None
    if post is None:
        post = getattr(bean_cls, POST_ATTR, None)
    roles = getattr(func, SECURED_ATTR, None) if func is not None else None
    if roles is None:
        roles = getattr(bean_cls, SECURED_ATTR, None)
    if pre is None and post is None and roles is None:
        return None
    return SecurityMeta(pre=pre, post=post, roles=roles)


def PreAuthorize(expression: str) -> Callable[[F], F]:
    """Run ``expression`` BEFORE the method; falsy denies the call.

    The expression is parsed now — bad grammar or unknown parameter
    references raise :class:`SecurityExpressionError` at decoration
    (function target) or at ``refresh()`` (class-level mark, checked per
    covered method's signature).
    """
    parsed = ParsedExpression(expression, post=False)

    def wrap(obj: F) -> F:
        if inspect.isfunction(obj) or inspect.ismethod(obj):
            owner = getattr(obj, "__qualname__", repr(obj))
            parsed.validate_params(obj, f"@PreAuthorize {owner}")
        setattr(obj, PRE_ATTR, parsed)
        return obj

    return wrap


def PostAuthorize(expression: str) -> Callable[[F], F]:
    """Run ``expression`` AFTER the method returns; falsy denies.

    The return value is available as ``return_value``/``result``/
    ``#result`` alongside the bound arguments.
    """
    parsed = ParsedExpression(expression, post=True)

    def wrap(obj: F) -> F:
        if inspect.isfunction(obj) or inspect.ismethod(obj):
            owner = getattr(obj, "__qualname__", repr(obj))
            parsed.validate_params(obj, f"@PostAuthorize {owner}")
        setattr(obj, POST_ATTR, parsed)
        return obj

    return wrap


def Secured(*roles: str) -> Callable[[F], F]:
    """Roles shorthand: pass when the caller holds ANY listed role."""
    if not roles or not all(isinstance(r, str) for r in roles):
        raise SecurityExpressionError(
            "@Secured requires at least one role name string"
        )
    granted = tuple(roles)

    def wrap(obj: F) -> F:
        setattr(obj, SECURED_ATTR, granted)
        return obj

    return wrap


def _deny(auth: Authentication | None, message: str) -> None:
    """A falsy check: no usable auth -> 401-type, else 403-type."""
    if auth is None or not auth.authenticated:
        raise AuthenticationError(message)
    raise AccessDeniedError(message)


def _secured_match(
    bean_cls: type[Any], method_name: str, func: Callable[..., Any]
) -> bool:
    """Pointcut predicate: does this method carry a security mark?

    Raises at ``refresh()`` (once per method) for generator targets and
    for expressions referencing parameters the method does not declare.
    """
    meta = security_meta(bean_cls, func)
    if meta is None:
        return False
    owner = f"{bean_cls.__name__}.{method_name}"
    if inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func):
        raise TypeError(
            f"@PreAuthorize/@PostAuthorize/@Secured {owner}:"
            " generator functions cannot be secured"
        )
    for expr in (meta.pre, meta.post):
        if expr is not None:
            expr.validate_params(func, owner)
    return True


_SECURED_METHOD: Pointcut = Pointcut(
    _secured_match, "@PreAuthorize/@PostAuthorize/@Secured"
)
_IS_SYNC: Pointcut = Pointcut(
    lambda _c, _n, f: not asyncio.iscoroutinefunction(f), "sync-method"
)
_IS_ASYNC: Pointcut = Pointcut(
    lambda _c, _n, f: asyncio.iscoroutinefunction(f), "async-method"
)


def _meta_for(invocation: Invocation) -> SecurityMeta | None:
    """Re-resolve the effective metadata for a live invocation."""
    cls = type(invocation.target)
    func = getattr(cls, invocation.method_name, None)
    if func is None or not callable(func):
        return None
    return security_meta(cls, func)


@Aspect
@Order(DEFAULT_SECURITY_ORDER)
class MethodSecurityInterceptor:
    """``@Around`` advice enforcing the method-security markers.

    @Order(50) — outside the tx interceptor (@Order(100)): the security
    check runs before the transaction boundary, so denied calls never
    open a transaction. Method signatures are bound lazily per method
    and cached, like the tx interceptor's manager resolution.
    """

    def __init__(self) -> None:
        self._signatures: dict[tuple[type[Any], str], inspect.Signature] = {}

    @Around(_SECURED_METHOD & _IS_SYNC)
    def invoke(self, invocation: Invocation) -> Any:
        """Synchronous security boundary around the target call."""
        meta = _meta_for(invocation)
        if meta is None:
            return invocation.proceed()
        owner = f"{type(invocation.target).__name__}.{invocation.method_name}"
        auth = SecurityContextHolder.get_authentication()
        args = self._bound_args(invocation)
        self._check_pre(meta, auth, args, owner)
        result = invocation.proceed()
        self._check_post(meta, auth, args, result, owner)
        return result

    @Around(_SECURED_METHOD & _IS_ASYNC)
    async def invoke_async(self, invocation: Invocation) -> Any:
        """Asynchronous security boundary around the target call."""
        meta = _meta_for(invocation)
        if meta is None:
            return await invocation.proceed()
        owner = f"{type(invocation.target).__name__}.{invocation.method_name}"
        auth = SecurityContextHolder.get_authentication()
        args = self._bound_args(invocation)
        self._check_pre(meta, auth, args, owner)
        result = await invocation.proceed()
        self._check_post(meta, auth, args, result, owner)
        return result

    def _bound_args(self, invocation: Invocation) -> dict[str, Any]:
        """Bind call args to parameter names via the method signature."""
        key = (type(invocation.target), invocation.method_name)
        signature = self._signatures.get(key)
        if signature is None:
            func = getattr(key[0], key[1])
            signature = inspect.signature(func)
            self._signatures[key] = signature
        bound = signature.bind(
            invocation.target, *invocation.args, **invocation.kwargs
        )
        args: dict[str, Any] = {}
        for name, value in bound.arguments.items():
            if name == "self":
                continue
            param = signature.parameters.get(name)
            if (
                param is not None
                and param.kind is inspect.Parameter.VAR_KEYWORD
                and isinstance(value, dict)
            ):
                # Flatten **kwargs so '#name' sees the actual call kwargs.
                args.update(value)
            else:
                args[name] = value
        return args

    @staticmethod
    def _check_pre(
        meta: SecurityMeta,
        auth: Authentication | None,
        args: dict[str, Any],
        owner: str,
    ) -> None:
        if meta.roles is not None:
            granted = (
                auth is not None
                and auth.authenticated
                and any(auth.has_role(r) for r in meta.roles)
            )
            if not granted:
                _deny(auth, f"{owner}: @Secured{meta.roles} denied")
        if meta.pre is not None and not meta.pre.check(auth, args):
            _deny(auth, f"{owner}: @PreAuthorize({meta.pre.source!r}) denied")

    @staticmethod
    def _check_post(
        meta: SecurityMeta,
        auth: Authentication | None,
        args: dict[str, Any],
        result: Any,
        owner: str,
    ) -> None:
        if meta.post is not None and not meta.post.check(
            auth, args, result=result
        ):
            _deny(
                auth,
                f"{owner}: @PostAuthorize({meta.post.source!r}) denied",
            )


__all__ = [
    "DEFAULT_SECURITY_ORDER",
    "POST_ATTR",
    "PRE_ATTR",
    "SECURED_ATTR",
    "SECURITY_ORDER_PROPERTY",
    "MethodSecurityInterceptor",
    "PostAuthorize",
    "PreAuthorize",
    "Secured",
    "SecurityMeta",
    "security_meta",
]
