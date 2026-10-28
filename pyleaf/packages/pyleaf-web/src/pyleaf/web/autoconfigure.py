"""Web auto-configuration: the FastAPI app and a shared HTTP client.

Registered through the ``pyleaf.autoconfigure`` entry point. Two beans:

- ``fastapiApp`` (:class:`fastapi.FastAPI`): built when no user ``FastAPI``
  bean exists. The app mounts every ``@RestController`` route, the
  ``@ControllerAdvice`` exception dispatcher and every ``@WebMiddleware``
  bean, plus the always-on :class:`RequestScopeMiddleware`; CORS middleware
  is added from ``pyleaf.web.cors.*`` properties when
  ``pyleaf.web.cors.allowed-origins`` is set; a plain ``/health`` readiness
  endpoint returns ``{"status": "UP"}``. The app lifespan is the single
  close owner when served: on shutdown it closes async resources (the
  shared ``httpx.AsyncClient``) while the loop is still alive, then calls
  ``ctx.close()``.
- ``httpClient`` (:class:`httpx.AsyncClient`): a shared client, backing off
  to a user bean. ``AsyncClient.aclose`` is async while ``ctx.close()``
  runs sync ``@PreDestroy`` hooks, so the managed subclass schedules the
  close on the running loop or drives it with ``asyncio.run``.

Condition guard rails mirror the sqlalchemy starter: ``@ConditionalOnClass``
is repeated on each ``@Bean`` method because core does not propagate
class-level conditions to bean-method definitions — a method def that
outlived its dropped configuration def would fail at eager creation. The
``pyleaf.web.enabled`` gate is a custom condition rather than
``@ConditionalOnProperty`` so a YAML ``enabled: false`` bool (not only the
string ``"false"``) also disables the beans; ``run()`` additionally checks
it to suppress serving.

The ``ctx`` parameter of ``fastapiApp`` resolves through the internal
``applicationContext`` bean registered by
:class:`pyleaf.core.ApplicationContext` itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, ClassVar

from pyleaf.boot import AutoConfiguration
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Conditional,
    ConditionalOnClass,
    ConditionalOnMissingBean,
    ConditionContext,
    ConditionResult,
    Environment,
    PreDestroy,
)
from starlette.middleware.cors import CORSMiddleware

from .advice import register_advice
from .customizer import FastApiCustomizer
from .middleware import RequestScopeMiddleware, register_middleware
from .router import register_controllers

try:
    from fastapi import FastAPI
except ImportError:  # pragma: no cover - requires a fastapi-less env
    # Placeholder keeps the @Bean annotations resolvable when fastapi is
    # absent; the defs they annotate are dropped by @ConditionalOnClass.
    class FastAPI:  # type: ignore[no-redef]
        """Placeholder for ``fastapi.FastAPI``."""


try:
    from httpx import AsyncClient
except ImportError:  # pragma: no cover - requires an httpx-less env
    class AsyncClient:  # type: ignore[no-redef]
        """Placeholder for ``httpx.AsyncClient``."""


WEB_ENABLED_PROPERTY = "pyleaf.web.enabled"
CORS_ORIGINS_PROPERTY = "pyleaf.web.cors.allowed-origins"
CORS_METHODS_PROPERTY = "pyleaf.web.cors.allowed-methods"
CORS_HEADERS_PROPERTY = "pyleaf.web.cors.allowed-headers"
CORS_CREDENTIALS_PROPERTY = "pyleaf.web.cors.allow-credentials"
CORS_MAX_AGE_PROPERTY = "pyleaf.web.cors.max-age"
DEFAULT_CORS_MAX_AGE = 600

# Close tasks scheduled on a foreign loop: kept until done so they are not
# garbage-collected mid-flight (RUF006-style dangling-task prevention).
_PENDING_CLOSES: set[asyncio.Task[None]] = set()


class _WebEnabled:
    """Match unless ``pyleaf.web.enabled`` is explicitly false.

    ``@ConditionalOnProperty`` compares raw string values, so a YAML
    ``enabled: false`` bool would never equal ``"false"``; going through
    the Environment's bool coercion handles every source uniformly.
    """

    bean_presence: ClassVar[bool] = False

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        enabled = bool(ctx.environment.get(WEB_ENABLED_PROPERTY, bool, True))
        verdict = "enabled" if enabled else "disabled"
        return ConditionResult(
            enabled, f"'{WEB_ENABLED_PROPERTY}' {verdict}"
        )


class _ManagedAsyncClient(AsyncClient):
    """An ``httpx.AsyncClient`` whose ``@PreDestroy`` drives ``aclose()``.

    ``ctx.close()`` calls destroy hooks synchronously. When a loop is
    running (e.g. the uvicorn loop during lifespan shutdown) the async
    close is scheduled as a task; without a loop it runs via
    ``asyncio.run``.
    """

    @PreDestroy
    def _pyleaf_close(self) -> None:
        if self.is_closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        task = loop.create_task(self.aclose())
        _PENDING_CLOSES.add(task)
        task.add_done_callback(_PENDING_CLOSES.discard)


def _csv(value: Any) -> list[str]:
    """Normalize a property to a string list: comma-split, pass through."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
    return [str(value)]


def _add_cors(app: FastAPI, env: Environment) -> None:
    """Mount ``CORSMiddleware`` from ``pyleaf.web.cors.*`` when origins set."""
    origins = _csv(env.get(CORS_ORIGINS_PROPERTY))
    if not origins:
        return
    methods = _csv(env.get(CORS_METHODS_PROPERTY)) or ["*"]
    headers = _csv(env.get(CORS_HEADERS_PROPERTY)) or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=methods,
        allow_headers=headers,
        allow_credentials=bool(
            env.get(CORS_CREDENTIALS_PROPERTY, bool, False)
        ),
        max_age=int(env.get(CORS_MAX_AGE_PROPERTY, int, DEFAULT_CORS_MAX_AGE)),
    )


def _make_lifespan(
    ctx: ApplicationContext,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """App lifespan closing the context — the single close owner when served.

    ``AsyncClient`` beans are closed while the event loop is still running
    (their sync ``@PreDestroy`` could only schedule a task); ``ctx.close()``
    then runs the remaining sync destroy hooks.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        try:
            for instance in list(ctx._singletons.values()):
                if isinstance(instance, AsyncClient):
                    await instance.aclose()
        finally:
            ctx.close()

    return lifespan


@AutoConfiguration
@ConditionalOnClass("fastapi")
@Conditional(_WebEnabled)
class WebAutoConfiguration:
    """Auto-configured FastAPI app + shared ``httpx.AsyncClient`` beans."""

    @Bean
    @ConditionalOnClass("fastapi")
    @Conditional(_WebEnabled)
    @ConditionalOnMissingBean(FastAPI)
    def fastapiApp(self, ctx: ApplicationContext) -> FastAPI:
        """Build the app: controllers, advice, middleware, CORS, /health,
        then every ``FastApiCustomizer`` bean in @Order."""
        app = FastAPI(lifespan=_make_lifespan(ctx))
        register_controllers(app, ctx)
        register_advice(app, ctx)
        register_middleware(app, ctx)
        app.add_middleware(RequestScopeMiddleware, context=ctx)
        _add_cors(app, ctx.environment)

        @app.get("/health")
        def health() -> dict[str, str]:
            return {"status": "UP"}

        # Extension point: customizer beans (e.g. the actuator's) apply
        # after all built-in wiring, ordered by @Order then bean name.
        for customizer in ctx.get_all(FastApiCustomizer):
            customizer.customize(app)

        return app

    @Bean
    @ConditionalOnClass("fastapi")
    @ConditionalOnClass("httpx")
    @Conditional(_WebEnabled)
    @ConditionalOnMissingBean(AsyncClient)
    def httpClient(self) -> AsyncClient:
        """Shared async HTTP client, closed on ``ctx.close()``."""
        return _ManagedAsyncClient()


__all__ = ["WebAutoConfiguration"]
