"""Contract tests for ``/beans`` (no instantiation) and ``/conditions``."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyleaf.actuator import ActuatorAutoConfiguration
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Component,
    ConditionalOnProperty,
    Configuration,
    Environment,
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


def _beans(client: TestClient) -> dict[str, dict[str, Any]]:
    resp = client.get("/actuator/beans")
    assert resp.status_code == 200
    return {entry["name"]: entry for entry in resp.json()}


def test_beans_lists_lazy_singleton_without_creating_it() -> None:
    created: list[str] = []

    @Component
    class LazyThing:
        def __init__(self) -> None:
            created.append("lazyThing")

    ctx = _ctx(LazyThing, WebAutoConfiguration, ActuatorAutoConfiguration)
    beans = _beans(TestClient(ctx.get(FastAPI)))

    entry = beans["lazyThing"]
    assert entry["type"].endswith("LazyThing")
    assert entry["scope"] == "singleton"
    assert entry["primary"] is False
    assert entry["lazy"] is True
    assert entry["dependencies"] == []
    # Enumerating definitions must not instantiate the bean.
    assert created == []

    ctx.get_bean("lazyThing")
    assert created == ["lazyThing"]


def test_beans_reports_dependencies() -> None:
    @Component
    class Store:
        pass

    @Component
    class Service:
        def __init__(self, store: Store) -> None:
            self.store = store

    ctx = _ctx(Store, Service, WebAutoConfiguration, ActuatorAutoConfiguration)
    beans = _beans(TestClient(ctx.get(FastAPI)))

    assert beans["service"]["dependencies"] == ["store"]
    fastapi_deps = set(beans["fastapiApp"]["dependencies"])
    assert {"applicationContext", "webAutoConfiguration"} <= fastapi_deps


def test_conditions_renders_report_entries() -> None:
    @Configuration
    class Gated:
        @Bean
        @ConditionalOnProperty("feature.flag", having_value="on")
        def gatedFeature(self) -> str:
            return "enabled"

    ctx = _ctx(Gated, WebAutoConfiguration, ActuatorAutoConfiguration)
    ctx.record_condition(
        "manual.check", False, ["simulated out-of-band failure"]
    )
    resp = TestClient(ctx.get(FastAPI)).get("/actuator/conditions")

    assert resp.status_code == 200
    entries = {entry["name"]: entry for entry in resp.json()}
    gated = entries["gatedFeature"]
    assert gated["matched"] is False
    assert any("feature.flag" in reason for reason in gated["reasons"])
    # Out-of-band record_condition entries are rendered too.
    manual = entries["manual.check"]
    assert manual["matched"] is False
    assert manual["reasons"] == ["simulated out-of-band failure"]
