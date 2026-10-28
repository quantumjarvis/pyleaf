"""Contract tests for ``pyleaf.web.autoconfigure.WebAutoConfiguration``."""

import asyncio
import importlib.util
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyleaf.boot import discover_autoconfigs
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Component,
    Configuration,
    Environment,
    NoSuchBean,
    PreDestroy,
    PropertySource,
)
from pyleaf.web import GetMapping, RequestScopeMiddleware, RestController
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


def _report(ctx: ApplicationContext) -> dict[str, Any]:
    return {entry.bean_name: entry for entry in ctx.condition_report.entries}


def test_entry_point_discovers_web_autoconfiguration() -> None:
    """The ``web`` entry point loads WebAutoConfiguration."""
    discovered = discover_autoconfigs()

    assert discovered["web"] is WebAutoConfiguration


def test_fastapi_app_bean_mounts_controllers_middleware_and_health() -> None:
    @RestController("/greet")
    class GreetController:
        @GetMapping("")
        def greet(self) -> dict[str, str]:
            return {"msg": "hi"}

    ctx = _ctx(GreetController, WebAutoConfiguration)
    app = ctx.get(FastAPI)

    assert app is ctx.get_bean("fastapiApp")
    # RequestScopeMiddleware is mounted on every auto-configured app.
    assert any(
        middleware.cls is RequestScopeMiddleware
        for middleware in app.user_middleware
    )
    client = TestClient(app)
    resp = client.get("/greet")
    assert resp.status_code == 200
    assert resp.json() == {"msg": "hi"}
    # Readiness endpoint is always present.
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "UP"}


def test_cors_middleware_built_from_properties() -> None:
    ctx = _ctx(
        WebAutoConfiguration,
        values={
            "pyleaf.web.cors.allowed-origins": (
                "https://a.example, https://b.example"
            ),
            "pyleaf.web.cors.allow-credentials": "true",
        },
    )
    app = ctx.get(FastAPI)

    client = TestClient(app)
    resp = client.options(
        "/health",
        headers={
            "Origin": "https://a.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://a.example"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_no_cors_middleware_without_origins() -> None:
    from starlette.middleware.cors import CORSMiddleware

    ctx = _ctx(WebAutoConfiguration)
    app = ctx.get(FastAPI)

    assert not any(
        middleware.cls is CORSMiddleware for middleware in app.user_middleware
    )


def test_user_fastapi_bean_backs_off_with_onmissingbean_reason() -> None:
    @Configuration
    class UserApp:
        @Bean
        def myApp(self) -> FastAPI:
            return FastAPI()

    ctx = _ctx(UserApp, WebAutoConfiguration)

    app = ctx.get(FastAPI)
    assert app is ctx.get_bean("myApp")
    with pytest.raises(NoSuchBean):
        ctx.get_bean("fastapiApp")
    entry = _report(ctx)["fastapiApp"]
    assert entry.matched is False
    assert any(
        "@ConditionalOnMissingBean" in reason for reason in entry.reasons
    )


def test_user_async_client_bean_backs_off() -> None:
    @Configuration
    class UserClient:
        @Bean
        def myClient(self) -> httpx.AsyncClient:
            return httpx.AsyncClient()

    ctx = _ctx(UserClient, WebAutoConfiguration)

    client = ctx.get(httpx.AsyncClient)
    assert client is ctx.get_bean("myClient")
    with pytest.raises(NoSuchBean):
        ctx.get_bean("httpClient")
    ctx.close()
    asyncio.run(client.aclose())


def test_missing_fastapi_reports_onclass_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without fastapi every web def is dropped with an OnClass reason."""
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "fastapi" or name.startswith("fastapi."):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    ctx = _ctx(WebAutoConfiguration)

    entries = _report(ctx)
    for bean_name in ("webAutoConfiguration", "fastapiApp", "httpClient"):
        with pytest.raises(NoSuchBean):
            ctx.get_bean(bean_name)
        entry = entries[bean_name]
        assert entry.matched is False
        assert any(
            "is not importable" in reason for reason in entry.reasons
        ), f"{bean_name}: {entry.reasons}"


def test_web_enabled_false_drops_web_beans() -> None:
    ctx = _ctx(
        WebAutoConfiguration, values={"pyleaf.web.enabled": "false"}
    )

    for bean_name in ("webAutoConfiguration", "fastapiApp", "httpClient"):
        with pytest.raises(NoSuchBean):
            ctx.get_bean(bean_name)
    entries = _report(ctx)
    assert entries["webAutoConfiguration"].matched is False
    assert entries["fastapiApp"].matched is False
    assert entries["httpClient"].matched is False


def test_http_client_bean_closed_on_ctx_close() -> None:
    ctx = _ctx(WebAutoConfiguration)

    client = ctx.get(httpx.AsyncClient)
    assert isinstance(client, httpx.AsyncClient)

    ctx.close()

    assert client.is_closed


def test_lifespan_shutdown_closes_context() -> None:
    """The app lifespan is the single close owner when served."""
    destroyed: list[bool] = []

    @Component
    class Tracked:
        @PreDestroy
        def bye(self) -> None:
            destroyed.append(True)

    ctx = _ctx(Tracked, WebAutoConfiguration)
    ctx.get(Tracked)  # force-create the lazy singleton
    app = ctx.get(FastAPI)
    client = ctx.get(httpx.AsyncClient)

    with TestClient(app):
        pass  # entering/exiting runs the lifespan

    assert destroyed == [True]
    assert client.is_closed
