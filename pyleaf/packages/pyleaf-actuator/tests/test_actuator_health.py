"""Contract tests for the actuator ``/health`` endpoint and its gates."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyleaf.actuator import ActuatorAutoConfiguration, HealthIndicator
from pyleaf.boot import discover_autoconfigs
from pyleaf.core import (
    ApplicationContext,
    Component,
    Environment,
    NoSuchBean,
    PropertySource,
)
from pyleaf.web.autoconfigure import WebAutoConfiguration


def _ctx(
    *classes: type, values: dict[str, object] | None = None
) -> ApplicationContext:
    ctx = ApplicationContext(
        environment=Environment(
            sources=[PropertySource("test", values or {})]
        )
    )
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def test_entry_point_discovers_actuator_autoconfiguration() -> None:
    """The ``actuator`` entry point loads ActuatorAutoConfiguration."""
    discovered = discover_autoconfigs()

    assert discovered["actuator"] is ActuatorAutoConfiguration


def test_health_is_up_with_ping_component_by_default() -> None:
    ctx = _ctx(WebAutoConfiguration, ActuatorAutoConfiguration)
    resp = TestClient(ctx.get(FastAPI)).get("/actuator/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "UP"
    assert body["components"]["pingHealthIndicator"] == {"status": "UP"}


def test_down_indicator_flips_overall_to_503() -> None:
    @Component
    class DownIndicator:
        def health(self) -> dict[str, str]:
            return {"status": "DOWN", "cause": "db unreachable"}

    ctx = _ctx(DownIndicator, WebAutoConfiguration, ActuatorAutoConfiguration)
    resp = TestClient(ctx.get(FastAPI)).get("/actuator/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "DOWN"
    assert body["components"]["downIndicator"]["status"] == "DOWN"
    assert body["components"]["downIndicator"]["cause"] == "db unreachable"
    assert body["components"]["pingHealthIndicator"]["status"] == "UP"


def test_custom_indicator_discovered_structurally() -> None:
    """Any bean with a ``health()`` method is picked up — no inheritance."""

    @Component
    class CacheIndicator:
        def health(self) -> dict[str, str]:
            return {"status": "UP", "vendor": "in-memory"}

    ctx = _ctx(CacheIndicator, WebAutoConfiguration, ActuatorAutoConfiguration)
    body = TestClient(ctx.get(FastAPI)).get("/actuator/health").json()

    assert body["status"] == "UP"
    assert body["components"]["cacheIndicator"]["vendor"] == "in-memory"


def test_indicator_inheriting_the_protocol_also_works() -> None:
    @Component
    class TypedIndicator(HealthIndicator):
        def health(self) -> dict[str, str]:
            return {"status": "UP", "detail": "typed"}

    ctx = _ctx(TypedIndicator, WebAutoConfiguration, ActuatorAutoConfiguration)
    body = TestClient(ctx.get(FastAPI)).get("/actuator/health").json()

    assert body["status"] == "UP"
    assert body["components"]["typedIndicator"]["detail"] == "typed"


def test_disabled_health_endpoint_returns_404() -> None:
    ctx = _ctx(
        WebAutoConfiguration,
        ActuatorAutoConfiguration,
        values={"pyleaf.actuator.endpoints.health.enabled": "false"},
    )
    client = TestClient(ctx.get(FastAPI))

    assert client.get("/actuator/health").status_code == 404
    # Other endpoints stay mounted.
    assert client.get("/actuator/beans").status_code == 200


def test_custom_base_path_moves_all_endpoints() -> None:
    ctx = _ctx(
        WebAutoConfiguration,
        ActuatorAutoConfiguration,
        values={"pyleaf.actuator.base-path": "/ops"},
    )
    client = TestClient(ctx.get(FastAPI))

    assert client.get("/ops/health").status_code == 200
    assert client.get("/actuator/health").status_code == 404


def test_actuator_disabled_drops_beans_and_routes() -> None:
    ctx = _ctx(
        WebAutoConfiguration,
        ActuatorAutoConfiguration,
        values={"pyleaf.actuator.enabled": "false"},
    )

    for name in (
        "actuatorAutoConfiguration",
        "actuatorCustomizer",
        "pingHealthIndicator",
    ):
        with pytest.raises(NoSuchBean):
            ctx.get_bean(name)
    client = TestClient(ctx.get(FastAPI))
    assert client.get("/actuator/health").status_code == 404
