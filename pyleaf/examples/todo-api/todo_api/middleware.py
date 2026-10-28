"""RequestTimingMiddleware: request-id + timing headers on every response.

A ``@WebMiddleware`` bean: the class follows the Starlette
``BaseHTTPMiddleware`` contract (``async dispatch(request, call_next)``) but
is a real bean — the request-scoped ``RequestAudit`` proxy is injected
through the constructor and resolves per request.
"""

import time
from collections.abc import Awaitable, Callable

from pyleaf.web import WebMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .audit import RequestAudit

CallNext = Callable[[Request], Awaitable[Response]]


@WebMiddleware
class RequestTimingMiddleware:
    """Stamp ``x-request-id`` and ``x-response-time-ms`` on responses.

    Accessing ``self._audit.request_id`` materializes the request-scoped
    audit bean for this request (the ``RequestScopeMiddleware`` layer is
    outermost, so a scope is always active here).
    """

    def __init__(self, audit: RequestAudit) -> None:
        self._audit = audit

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        """Time the downstream call and decorate the response."""
        started = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - started) * 1000
        response.headers["x-request-id"] = self._audit.request_id
        response.headers["x-response-time-ms"] = f"{elapsed_ms:.1f}"
        return response
