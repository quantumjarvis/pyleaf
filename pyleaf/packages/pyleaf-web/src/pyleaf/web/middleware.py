"""Pure-ASGI middleware activating a ``RequestScope`` per HTTP request.

For every ``http`` ASGI scope the middleware activates a fresh
:class:`pyleaf.core.RequestScope` bound to the container, calls the
downstream app, then deactivates and closes the scope in ``finally`` —
so ``@PreDestroy`` hooks run once the response is sent (or an exception
propagates). Non-http scopes (websocket, lifespan) pass straight through
without a request scope.

Bean middleware
---------------
``@WebMiddleware`` marks a class (component stereotype, so constructor DI
applies) whose ``async dispatch(request, call_next) -> Response`` method
follows the Starlette ``BaseHTTPMiddleware`` contract. ``register_middleware``
resolves each bean and mounts its ``dispatch`` through
``BaseHTTPMiddleware``.

Ordering: Starlette inserts every ``add_middleware`` call at the FRONT of
the user middleware list, so the LAST class registered is the OUTERMOST
layer. ``register_middleware`` therefore adds beans in @Order-DESCENDING
order so that the LOWEST ``@Order`` value ends up outermost — it sees the
request first and the response last, mirroring Spring's ordered-filter
semantics. Ties keep registration order; unordered beans
(``DEFAULT_ORDER``) sort innermost.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar, overload

from pyleaf.core.definition import Scope
from pyleaf.core.scope import RequestScope
from pyleaf.core.stereotypes import COMPONENT_ATTR, ComponentMeta
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from fastapi import FastAPI
    from pyleaf.core.context import ApplicationContext
    from starlette.types import ASGIApp, Receive, Send
    from starlette.types import Scope as ASGIScope

T = TypeVar("T")

WEB_MIDDLEWARE_ATTR = "__pyleaf_middleware__"


class RequestScopeMiddleware:
    """Activate a fresh ``RequestScope`` around each HTTP request."""

    def __init__(self, app: ASGIApp, context: ApplicationContext) -> None:
        self.app = app
        self._context = context

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_scope = RequestScope(self._context)
        token = request_scope.activate()
        try:
            await self.app(scope, receive, send)
        finally:
            # Deactivate before close(): @PreDestroy hooks reaching for a
            # request bean see ScopeNotActive instead of silently creating
            # an instance nothing will destroy.
            RequestScope.deactivate(token)
            request_scope.close()


@overload
def WebMiddleware(target: type[T], /) -> type[T]: ...


@overload
def WebMiddleware(
    target: str | None = None,
    /,
    *,
    name: str | None = None,
) -> Callable[[type[T]], type[T]]: ...


def WebMiddleware(
    target: type[T] | str | None = None,
    /,
    *,
    name: str | None = None,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Mark a class as a bean middleware.

    Usable bare (``@WebMiddleware``) or with a bean name. The class must
    define ``async dispatch(self, request, call_next) -> Response`` —
    the Starlette ``BaseHTTPMiddleware`` contract — and registers through
    normal component dispatch, so constructor DI and ``@Order`` apply.
    ``register_middleware`` mounts it on the app.
    """
    effective = name if name is not None else (target if isinstance(target, str) else None)

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, WEB_MIDDLEWARE_ATTR, True)
        if getattr(cls, COMPONENT_ATTR, None) is None:
            setattr(cls, COMPONENT_ATTR, ComponentMeta(effective, Scope.SINGLETON, True))
        return cls

    if target is None or isinstance(target, str):
        return wrap
    return wrap(target)


def register_middleware(app: FastAPI, ctx: ApplicationContext) -> FastAPI:
    """Mount every ``@WebMiddleware`` bean's ``dispatch`` on ``app``.

    Beans are sorted by ``(@Order, registration order)`` and added in
    descending @Order, so the lowest @Order is outermost (see the module
    docstring). Middleware beans are real beans: ``ctx.get_bean`` runs
    constructor injection, post-processors and AOP before mounting.
    """
    defs = [
        defn
        for defn in ctx.bean_definitions()
        if isinstance(defn.bean_type, type)
        and getattr(defn.bean_type, WEB_MIDDLEWARE_ATTR, None) is not None
    ]
    # ``bean_definitions()`` is registration order; the sort is stable.
    defs.sort(key=lambda d: d.order)
    # Starlette's last-registered middleware is outermost; registering in
    # descending @Order puts the lowest @Order on the outside.
    for defn in reversed(defs):
        instance = ctx.get_bean(defn.name)
        dispatch: Any = getattr(instance, "dispatch", None)
        if not callable(dispatch):
            raise TypeError(
                f"@WebMiddleware bean '{defn.name}' has no callable "
                "'dispatch(request, call_next)' method"
            )
        app.add_middleware(BaseHTTPMiddleware, dispatch=dispatch)
    return app


__all__ = [
    "WEB_MIDDLEWARE_ATTR",
    "RequestScopeMiddleware",
    "WebMiddleware",
    "register_middleware",
]
