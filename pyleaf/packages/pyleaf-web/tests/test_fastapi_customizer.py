"""Contract tests for the ``FastApiCustomizer`` bean extension point."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyleaf.core import (
    ApplicationContext,
    Component,
    Environment,
    Order,
    PropertySource,
)
from pyleaf.web import FastApiCustomizer
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


def test_customizer_bean_adds_a_route() -> None:
    """A bean with a ``customize(app)`` method mounts extra routes."""

    @Component
    class ExtraRoutes:
        def customize(self, app: FastAPI) -> None:
            @app.get("/extra")
            def extra() -> dict[str, bool]:
                return {"ok": True}

    ctx = _ctx(ExtraRoutes, WebAutoConfiguration)
    app = ctx.get(FastAPI)

    resp = TestClient(app).get("/extra")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_customizers_apply_in_order() -> None:
    """``ctx.get_all`` ordering applies: @Order first, then bean name."""
    applied: list[str] = []

    @Order(2)
    @Component
    class SecondCustomizer:
        def customize(self, app: FastAPI) -> None:
            applied.append("second")

    @Order(1)
    @Component
    class FirstCustomizer:
        def customize(self, app: FastAPI) -> None:
            applied.append("first")

    ctx = _ctx(SecondCustomizer, FirstCustomizer, WebAutoConfiguration)
    ctx.get(FastAPI)

    assert applied == ["first", "second"]


def test_customizer_returned_by_bean_method_matches_the_protocol() -> None:
    """A ``@Bean``-method customizer is discovered by its return type."""

    from pyleaf.core import Bean, Configuration

    class _Routes:
        def customize(self, app: FastAPI) -> None:
            @app.get("/from-bean")
            def from_bean() -> dict[str, bool]:
                return {"ok": True}

    @Configuration
    class RouteConfig:
        @Bean
        def extraRoutes(self) -> FastApiCustomizer:
            return _Routes()

    ctx = _ctx(RouteConfig, WebAutoConfiguration)
    resp = TestClient(ctx.get(FastAPI)).get("/from-bean")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
