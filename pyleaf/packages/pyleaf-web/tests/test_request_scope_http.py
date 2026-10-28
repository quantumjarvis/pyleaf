"""RequestScopeMiddleware: per-request instances, teardown, concurrency."""

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from pyleaf.core import (
    ApplicationContext,
    Component,
    PreDestroy,
    RequestScope,
    Scope,
    ScopeNotActive,
)
from pyleaf.web import (
    GetMapping,
    RequestScopeMiddleware,
    RestController,
    register_controllers,
)

CREATED: list[str] = []
DESTROYED: list[str] = []


def _build() -> ApplicationContext:
    """Fresh ctx: one request bean + a singleton controller injecting it."""
    CREATED.clear()
    DESTROYED.clear()

    @Component(scope=Scope.REQUEST)
    class RequestBean:
        def __init__(self) -> None:
            self.ident = uuid.uuid4().hex
            CREATED.append(self.ident)

        @PreDestroy
        def teardown(self) -> None:
            DESTROYED.append(self.ident)

    @RestController("/rid")
    class RidController:
        def __init__(self, bean: RequestBean) -> None:
            self.bean = bean

        @GetMapping("")
        async def rid(self) -> dict[str, str]:
            # Yield so concurrent requests interleave inside their scopes.
            await asyncio.sleep(0)
            return {"id": self.bean.ident}

    @RestController("/pair")
    class PairController:
        def __init__(self, bean: RequestBean) -> None:
            self.bean = bean

        @GetMapping("")
        def pair(self) -> dict[str, str]:
            return {"a": self.bean.ident, "b": self.bean.ident}

    ctx = ApplicationContext()
    ctx.register(RequestBean)
    ctx.register(RidController)
    ctx.register(PairController)
    ctx.refresh()
    return ctx


def _app(ctx: ApplicationContext) -> RequestScopeMiddleware:
    app = FastAPI()
    register_controllers(app, ctx)
    return RequestScopeMiddleware(app, ctx)


def test_per_request_instance_and_intra_request_sharing() -> None:
    ctx = _build()
    client = TestClient(_app(ctx))

    first = client.get("/rid").json()["id"]
    second = client.get("/rid").json()["id"]
    assert first != second

    pair = client.get("/pair").json()
    # Two lookups inside one request resolve the same instance.
    assert pair["a"] == pair["b"]
    assert pair["a"] not in {first, second}


def test_predestroy_runs_after_response() -> None:
    ctx = _build()
    client = TestClient(_app(ctx))
    resp = client.get("/rid")
    assert resp.status_code == 200
    # The scope closed before TestClient returned: @PreDestroy already ran.
    assert DESTROYED == [resp.json()["id"]]


async def test_100_concurrent_requests_distinct_instances() -> None:
    ctx = _build()
    transport = httpx.ASGITransport(app=_app(ctx))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        responses = await asyncio.gather(*[client.get("/rid") for _ in range(100)])
    assert all(r.status_code == 200 for r in responses)
    ids = {r.json()["id"] for r in responses}
    assert len(ids) == 100
    # Every scope created exactly one instance and destroyed it afterwards.
    assert set(CREATED) == ids
    assert set(DESTROYED) == ids


def test_access_after_response_raises_scope_not_active() -> None:
    """Background-task simulation: the proxy fails once the scope closed."""
    ctx = _build()
    client = TestClient(_app(ctx))
    proxy = ctx.get_bean("requestBean")
    resp = client.get("/rid")
    assert resp.status_code == 200
    with pytest.raises(ScopeNotActive, match="requestBean"):
        _ = proxy.ident


def test_proxy_injected_into_singleton_is_shared() -> None:
    """The same proxy object serves every request; each sees its instance."""
    from pyleaf.core import RequestScopedProxy

    ctx = _build()
    client = TestClient(_app(ctx))
    proxy = ctx.get_bean("requestBean")
    assert isinstance(proxy, RequestScopedProxy)
    ids = {client.get("/rid").json()["id"] for _ in range(3)}
    assert len(ids) == 3


def test_websocket_passes_through_without_scope() -> None:
    ctx = _build()
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            RequestScope.current()
            await websocket.send_text("active")
        except ScopeNotActive:
            await websocket.send_text("inactive")
        await websocket.close()

    register_controllers(app, ctx)
    client = TestClient(RequestScopeMiddleware(app, ctx))
    with client.websocket_connect("/ws") as sock:
        assert sock.receive_text() == "inactive"


def test_http_still_works_when_response_raises() -> None:
    """An exception downstream still tears the scope down (finally)."""
    ctx = _build()
    app = FastAPI()

    @app.get("/boom")
    def boom() -> None:
        # Touch the request bean so it exists, then blow up.
        _ = ctx.get_bean("requestBean").ident
        raise RuntimeError("boom")

    register_controllers(app, ctx)
    client = TestClient(RequestScopeMiddleware(app, ctx), raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert len(DESTROYED) == 1
    assert DESTROYED == CREATED
