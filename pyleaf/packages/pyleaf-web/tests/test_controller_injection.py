"""Injection tests: constructor DI, ``Depends`` and AOP-advised handlers."""

import inspect
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Component,
    Invocation,
    Service,
    bean_type,
)
from pyleaf.web import GetMapping, RestController, build_router, register_controllers


def _app(ctx: ApplicationContext) -> FastAPI:
    app = FastAPI()
    register_controllers(app, ctx)
    return app


def test_constructor_di() -> None:
    @Service
    class GreetingService:
        def greet(self, name: str) -> str:
            return f"hi {name}"

    @RestController("/hello")
    class HelloController:
        def __init__(self, service: GreetingService) -> None:
            self._service = service

        @GetMapping("/{name}")
        def hello(self, name: str) -> dict[str, str]:
            return {"msg": self._service.greet(name)}

    ctx = ApplicationContext()
    ctx.register(GreetingService)
    ctx.register(HelloController)
    ctx.refresh()

    resp = TestClient(_app(ctx)).get("/hello/ada")
    assert resp.status_code == 200
    assert resp.json() == {"msg": "hi ada"}


def test_fastapi_depends_still_works() -> None:
    def trace_id() -> str:
        return "trace-1"

    @RestController("/dep")
    class DepController:
        @GetMapping("")
        def read(self, trace: str = Depends(trace_id)) -> dict[str, str]:
            return {"trace": trace}

    ctx = ApplicationContext()
    ctx.register(DepController)
    ctx.refresh()

    resp = TestClient(_app(ctx)).get("/dep")
    assert resp.status_code == 200
    assert resp.json() == {"trace": "trace-1"}


def test_advised_controller_method_routes() -> None:
    """An AOP-wrapped bound method keeps its signature and still routes."""

    calls: list[str] = []

    @RestController("/adv")
    class AdvisedController:
        @GetMapping("/{n}")
        def advised(self, n: int) -> dict[str, int]:
            """Advised handler."""
            return {"n": n * 2}

    @Aspect
    @Component
    class Tracing:
        @Around(bean_type(AdvisedController))
        def wrap(self, inv: Invocation) -> Any:
            calls.append(inv.method_name)
            return inv.proceed()

    ctx = ApplicationContext()
    ctx.register(Tracing)
    ctx.register(AdvisedController)
    ctx.refresh()

    bean = ctx.get(AdvisedController)
    # The instance attribute is the AOP wrapper; its signature (via
    # functools.wraps -> __wrapped__ -> bound method) exposes no ``self``.
    params = list(inspect.signature(bean.advised).parameters)
    assert params == ["n"]

    client = TestClient(_app(ctx))
    resp = client.get("/adv/3")
    assert resp.status_code == 200
    assert resp.json() == {"n": 6}
    assert calls == ["advised"]


def test_bean_name_and_singleton_semantics() -> None:
    """Controllers are ordinary singleton beans named like components."""

    @RestController("/named")
    class NamedController:
        @GetMapping("")
        def read(self) -> str:
            return "ok"

    ctx = ApplicationContext()
    ctx.register(NamedController)
    ctx.refresh()

    assert ctx.get_bean("namedController") is ctx.get(NamedController)
    # build_router resolves the same singleton — no second construction.
    router = build_router(ctx)
    assert len(router.routes) == 1
