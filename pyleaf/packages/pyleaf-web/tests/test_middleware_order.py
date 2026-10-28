"""@WebMiddleware beans: @Order defines onion position; constructor DI applies.

Ordering rule (mirrors Spring's ordered filters): the LOWEST @Order value is
the OUTERMOST middleware — it sees the request first and the response last.
Starlette inserts every ``add_middleware`` call at the front of the stack, so
``register_middleware`` adds beans in @Order-descending order to achieve that.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient
from pyleaf.core import ApplicationContext, Component, Order, Service
from pyleaf.web import WebMiddleware, register_middleware

CallNext = Callable[[Request], Awaitable[Response]]


@Component
class Trace:
    def __init__(self) -> None:
        self.calls: list[str] = []


def _build_ctx() -> ApplicationContext:
    @WebMiddleware
    @Order(30)
    class GammaMw:
        def __init__(self, trace: Trace) -> None:
            self.trace = trace

        async def dispatch(self, request: Request, call_next: CallNext) -> Response:
            self.trace.calls.append("gamma")
            resp = await call_next(request)
            resp.headers.append("x-mw", "gamma")
            return resp

    @WebMiddleware
    @Order(10)
    class AlphaMw:
        def __init__(self, trace: Trace) -> None:
            self.trace = trace

        async def dispatch(self, request: Request, call_next: CallNext) -> Response:
            self.trace.calls.append("alpha")
            resp = await call_next(request)
            resp.headers.append("x-mw", "alpha")
            return resp

    @WebMiddleware
    @Order(20)
    class BetaMw:
        def __init__(self, trace: Trace) -> None:
            self.trace = trace

        async def dispatch(self, request: Request, call_next: CallNext) -> Response:
            self.trace.calls.append("beta")
            resp = await call_next(request)
            resp.headers.append("x-mw", "beta")
            return resp

    ctx = ApplicationContext()
    ctx.register(Trace)
    # Scrambled registration order: @Order, not registration, must decide.
    ctx.register(GammaMw)
    ctx.register(AlphaMw)
    ctx.register(BetaMw)
    ctx.refresh()
    return ctx


def _app(ctx: ApplicationContext) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def root() -> dict[str, bool]:
        return {"ok": True}

    register_middleware(app, ctx)
    return app


def test_order_asc_is_outermost_on_request_path() -> None:
    ctx = _build_ctx()
    resp = TestClient(_app(ctx)).get("/")
    assert resp.status_code == 200
    trace = ctx.get_bean("trace")
    # Lowest @Order sees the request first (outermost -> innermost).
    assert trace.calls == ["alpha", "beta", "gamma"]


def test_response_headers_unwind_inner_to_outer() -> None:
    ctx = _build_ctx()
    resp = TestClient(_app(ctx)).get("/")
    # On the response path the innermost middleware appends first, so the
    # header sequence is the reverse of the request-path order.
    assert resp.headers.get_list("x-mw") == ["gamma", "beta", "alpha"]


def test_equal_order_keeps_registration_order() -> None:
    @WebMiddleware
    class First:
        async def dispatch(self, request: Request, call_next: CallNext) -> Response:
            resp = await call_next(request)
            resp.headers.append("x-eq", "first")
            return resp

    @WebMiddleware
    class Second:
        async def dispatch(self, request: Request, call_next: CallNext) -> Response:
            resp = await call_next(request)
            resp.headers.append("x-eq", "second")
            return resp

    ctx = ApplicationContext()
    ctx.register(First)
    ctx.register(Second)
    ctx.refresh()

    resp = TestClient(_app(ctx)).get("/")
    # Both unordered -> equal precedence; 'first' registered first so it is
    # outer -> its header lands last on the unwind.
    assert resp.headers.get_list("x-eq") == ["second", "first"]


def test_middleware_bean_supports_constructor_di() -> None:
    @Service
    class Banner:
        text = "hello-di"

    @WebMiddleware
    class DiMw:
        def __init__(self, banner: Banner) -> None:
            self.banner = banner

        async def dispatch(self, request: Request, call_next: CallNext) -> Response:
            resp = await call_next(request)
            resp.headers["x-banner"] = self.banner.text
            return resp

    ctx = ApplicationContext()
    ctx.register(Banner)
    ctx.register(DiMw)
    ctx.refresh()

    resp = TestClient(_app(ctx)).get("/")
    assert resp.headers["x-banner"] == "hello-di"


def test_unordered_middleware_is_innermost() -> None:
    @Component
    class Rec:
        def __init__(self) -> None:
            self.calls: list[str] = []

    @WebMiddleware
    @Order(1)
    class Ordered:
        def __init__(self, rec: Rec) -> None:
            self.rec = rec

        async def dispatch(self, request: Request, call_next: CallNext) -> Response:
            self.rec.calls.append("ordered")
            return await call_next(request)

    @WebMiddleware
    class Plain:
        def __init__(self, rec: Rec) -> None:
            self.rec = rec

        async def dispatch(self, request: Request, call_next: CallNext) -> Response:
            self.rec.calls.append("plain")
            return await call_next(request)

    ctx = ApplicationContext()
    ctx.register(Rec)
    ctx.register(Plain)  # unordered bean registered first on purpose
    ctx.register(Ordered)
    ctx.refresh()

    resp = TestClient(_app(ctx)).get("/")
    assert resp.status_code == 200
    # DEFAULT_ORDER sorts after @Order(1), so 'plain' is innermost.
    assert ctx.get_bean("rec").calls == ["ordered", "plain"]


def test_register_middleware_without_beans_is_noop() -> None:
    ctx = ApplicationContext()
    ctx.refresh()
    resp = TestClient(_app(ctx)).get("/")
    assert resp.json() == {"ok": True}
    assert "x-mw" not in resp.headers
