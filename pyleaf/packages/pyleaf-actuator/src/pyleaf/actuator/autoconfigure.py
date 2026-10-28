"""Actuator auto-configuration: operational endpoints on the web app.

Registered through the ``pyleaf.autoconfigure`` entry point. Two beans:

- ``actuatorCustomizer`` — a :class:`pyleaf.web.FastApiCustomizer` that
  mounts an ``APIRouter`` under ``pyleaf.actuator.base-path`` (default
  ``/actuator``) with one route per *enabled* endpoint. Each of
  ``health``, ``info``, ``env``, ``beans``, ``conditions`` and
  ``metrics`` is gated by
  ``pyleaf.actuator.endpoints.<name>.enabled`` (default true; disabled
  -> route absent -> 404). ``/metrics`` additionally requires the
  optional ``prometheus_client`` package — absent lib, no route.
- ``pingHealthIndicator`` — the built-in always-UP
  :class:`pyleaf.actuator.HealthIndicator`.

As in the other auto-configurations, ``@ConditionalOnClass`` and the
``pyleaf.actuator.enabled`` gate (a custom condition, so a YAML
``enabled: false`` bool disables too) are repeated on every ``@Bean``
method — core does not propagate class-level conditions to bean-method
definitions.

The fastapi imports are guarded so the module stays importable without
fastapi: ``@Bean`` annotations resolve eagerly at ``ctx.register`` time,
before ``@ConditionalOnClass`` drops the defs.
"""

from __future__ import annotations

import importlib.util
from typing import Any, ClassVar

from pyleaf.boot import AutoConfiguration, AutoConfigureAfter
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Conditional,
    ConditionalOnClass,
    ConditionContext,
    ConditionResult,
    Environment,
)
from pyleaf.web import FastApiCustomizer
from pyleaf.web.autoconfigure import WebAutoConfiguration

from .endpoints import (
    beans_payload,
    conditions_payload,
    env_payload,
    health_payload,
    info_payload,
)
from .health import HealthIndicator, PingHealthIndicator

try:
    from fastapi import APIRouter, FastAPI, Response
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover - requires a fastapi-less env
    # Placeholders keep the @Bean annotations resolvable when fastapi is
    # absent; the defs they annotate are dropped by @ConditionalOnClass.
    class FastAPI:  # type: ignore[no-redef]
        """Placeholder for ``fastapi.FastAPI``."""

    class APIRouter:  # type: ignore[no-redef]
        """Placeholder for ``fastapi.APIRouter``."""

    class Response:  # type: ignore[no-redef]
        """Placeholder for ``starlette.responses.Response``."""

    class JSONResponse(Response):  # type: ignore[no-redef]
        """Placeholder for ``starlette.responses.JSONResponse``."""


ACTUATOR_ENABLED_PROPERTY = "pyleaf.actuator.enabled"
BASE_PATH_PROPERTY = "pyleaf.actuator.base-path"
DEFAULT_BASE_PATH = "/actuator"
ENDPOINT_ENABLED_PREFIX = "pyleaf.actuator.endpoints"


class _ActuatorEnabled:
    """Match unless ``pyleaf.actuator.enabled`` is explicitly false.

    Mirrors ``_WebEnabled`` in pyleaf-web: ``@ConditionalOnProperty``
    compares raw string values, so a YAML ``enabled: false`` bool would
    never equal ``"false"``; the Environment's bool coercion handles
    every source uniformly.
    """

    bean_presence: ClassVar[bool] = False

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        enabled = bool(ctx.environment.get(ACTUATOR_ENABLED_PROPERTY, bool, True))
        verdict = "enabled" if enabled else "disabled"
        return ConditionResult(
            enabled, f"'{ACTUATOR_ENABLED_PROPERTY}' {verdict}"
        )


def _endpoint_enabled(env: Environment, name: str) -> bool:
    """True unless ``pyleaf.actuator.endpoints.<name>.enabled`` is false."""
    return bool(
        env.get(f"{ENDPOINT_ENABLED_PREFIX}.{name}.enabled", bool, True)
    )


def _prometheus_available() -> bool:
    """True when the optional ``prometheus_client`` package is installed."""
    try:
        return importlib.util.find_spec("prometheus_client") is not None
    except Exception:
        return False


class ActuatorCustomizer:
    """The ``FastApiCustomizer`` that mounts the actuator ``APIRouter``."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def customize(self, app: FastAPI) -> None:
        """Mount one route per enabled endpoint under the base path."""
        env = self._ctx.environment
        base = str(env.get(BASE_PATH_PROPERTY) or DEFAULT_BASE_PATH)
        router = APIRouter(prefix=base)

        if _endpoint_enabled(env, "health"):

            @router.get("/health")
            def actuator_health() -> Response:
                status_code, body = health_payload(self._ctx)
                return JSONResponse(body, status_code=status_code)

        if _endpoint_enabled(env, "info"):

            @router.get("/info")
            def actuator_info() -> dict[str, Any]:
                return info_payload(self._ctx)

        if _endpoint_enabled(env, "env"):

            @router.get("/env")
            def actuator_env() -> list[dict[str, Any]]:
                return env_payload(self._ctx)

        if _endpoint_enabled(env, "beans"):

            @router.get("/beans")
            def actuator_beans() -> list[dict[str, Any]]:
                return beans_payload(self._ctx)

        if _endpoint_enabled(env, "conditions"):

            @router.get("/conditions")
            def actuator_conditions() -> list[dict[str, Any]]:
                return conditions_payload(self._ctx)

        if _endpoint_enabled(env, "metrics") and _prometheus_available():
            import prometheus_client

            @router.get("/metrics")
            def actuator_metrics() -> Response:
                return Response(
                    content=prometheus_client.generate_latest(),
                    media_type=prometheus_client.CONTENT_TYPE_LATEST,
                )

        app.include_router(router)


@AutoConfiguration
@AutoConfigureAfter(WebAutoConfiguration)
@ConditionalOnClass("fastapi")
@Conditional(_ActuatorEnabled)
class ActuatorAutoConfiguration:
    """Mount the actuator endpoints and register ``pingHealthIndicator``."""

    @Bean
    @ConditionalOnClass("fastapi")
    @Conditional(_ActuatorEnabled)
    def actuatorCustomizer(self, ctx: ApplicationContext) -> FastApiCustomizer:
        """The customizer bean that mounts the actuator ``APIRouter``."""
        return ActuatorCustomizer(ctx)

    @Bean
    @ConditionalOnClass("fastapi")
    @Conditional(_ActuatorEnabled)
    def pingHealthIndicator(self) -> HealthIndicator:
        """Always-UP built-in health component."""
        return PingHealthIndicator()


__all__ = ["ActuatorAutoConfiguration", "ActuatorCustomizer"]
