"""Contract tests for the prometheus-backed ``/metrics`` endpoint."""

import importlib.util
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyleaf.actuator import ActuatorAutoConfiguration
from pyleaf.core import (
    ApplicationContext,
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


def test_metrics_returns_prometheus_text() -> None:
    ctx = _ctx(WebAutoConfiguration, ActuatorAutoConfiguration)
    resp = TestClient(ctx.get(FastAPI)).get("/actuator/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# TYPE" in resp.text


def test_disabled_metrics_endpoint_is_404_only() -> None:
    ctx = _ctx(
        WebAutoConfiguration,
        ActuatorAutoConfiguration,
        values={"pyleaf.actuator.endpoints.metrics.enabled": "false"},
    )
    client = TestClient(ctx.get(FastAPI))

    assert client.get("/actuator/metrics").status_code == 404
    assert client.get("/actuator/health").status_code == 200


def test_metrics_absent_when_prometheus_client_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without prometheus_client the route is never mounted (OnClass gate)."""
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "prometheus_client":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    ctx = _ctx(WebAutoConfiguration, ActuatorAutoConfiguration)
    client = TestClient(ctx.get(FastAPI))
    assert client.get("/actuator/metrics").status_code == 404
    assert client.get("/actuator/health").status_code == 200
