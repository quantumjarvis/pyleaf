"""Pure-ASGI middleware activating a ``RequestScope`` per HTTP request.

For every ``http`` ASGI scope the middleware activates a fresh
:class:`pyleaf.core.RequestScope` bound to the container, calls the
downstream app, then deactivates and closes the scope in ``finally`` —
so ``@PreDestroy`` hooks run once the response is sent (or an exception
propagates). Non-http scopes (websocket, lifespan) pass straight through
without a request scope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyleaf.core.scope import RequestScope

if TYPE_CHECKING:
    from pyleaf.core.context import ApplicationContext
    from starlette.types import ASGIApp, Receive, Scope, Send


class RequestScopeMiddleware:
    """Activate a fresh ``RequestScope`` around each HTTP request."""

    def __init__(self, app: ASGIApp, context: ApplicationContext) -> None:
        self.app = app
        self._context = context

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
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


__all__ = ["RequestScopeMiddleware"]
