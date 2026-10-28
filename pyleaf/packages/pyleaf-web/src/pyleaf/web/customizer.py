"""``FastApiCustomizer``: beans that tweak the auto-configured FastAPI app.

A bean whose class provides ``customize(app)`` — a structural
``typing.Protocol`` match, so inheriting the protocol is optional — is
applied by ``WebAutoConfiguration.fastapiApp`` after the built-in wiring
(controllers, advice, middleware, CORS, ``/health``). ``ctx.get_all``
returns customizers ordered by ``@Order`` then bean name, so extensions
stack deterministically. This is the decoupled hook through which
``pyleaf-actuator`` mounts its endpoints.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

try:
    from fastapi import FastAPI
except ImportError:  # pragma: no cover - requires a fastapi-less env
    # Placeholder keeps annotations resolvable when fastapi is absent.
    class FastAPI:  # type: ignore[no-redef]
        """Placeholder for ``fastapi.FastAPI``."""


@runtime_checkable
class FastApiCustomizer(Protocol):
    """Mutate the auto-configured ``FastAPI`` app before it is served."""

    def customize(self, app: FastAPI) -> None:
        """Apply custom wiring (routes, handlers) to ``app``."""
        ...


__all__ = ["FastApiCustomizer"]
