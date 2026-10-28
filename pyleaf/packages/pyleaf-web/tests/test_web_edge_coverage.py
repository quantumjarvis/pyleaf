"""Edge-case coverage for pyleaf.web internals.

Branches the happy-path suites don't reach: ``_csv`` scalar/iterable
normalization, ``_ManagedAsyncClient``'s running-loop close, the named
``@WebMiddleware`` / ``@ControllerAdvice`` factory forms, the no-``dispatch``
TypeError, ``_join``'s leading-slash fix-up, an explicit ``response_model``,
advice parameter resolution by conventional names / unresolvable
annotations, a sync handler returning an awaitable, and the
response-already-started re-raise in the advice dispatcher.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pyleaf.core import ApplicationContext
from pyleaf.core.stereotypes import COMPONENT_ATTR
from pyleaf.web import (
    ControllerAdvice,
    ExceptionHandler,
    GetMapping,
    RestController,
    WebMiddleware,
    register_advice,
    register_controllers,
    register_middleware,
)
from pyleaf.web.advice import CONTROLLER_ADVICE_ATTR
from pyleaf.web.autoconfigure import _PENDING_CLOSES, _csv, _ManagedAsyncClient
from pyleaf.web.middleware import WEB_MIDDLEWARE_ATTR
from pyleaf.web.router import _join
from starlette.responses import Response, StreamingResponse

CallNext = Callable[[Request], Awaitable[Response]]


def _ctx(*classes: type) -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def _app(ctx: ApplicationContext) -> FastAPI:
    app = FastAPI()
    register_controllers(app, ctx)
    register_advice(app, ctx)
    return app


def test_csv_normalizes_scalar_iterable_and_string_values() -> None:
    assert _csv(None) == []
    assert _csv("a, b ,, c") == ["a", "b", "c"]
    # Any non-string iterable is str()'d item by item...
    assert _csv(["x", "y"]) == ["x", "y"]
    assert _csv((1, "t")) == ["1", "t"]
    # ...and a non-iterable scalar is wrapped.
    assert _csv(7) == ["7"]


async def test_managed_client_close_schedules_task_on_running_loop() -> None:
    """With a running loop the sync @PreDestroy schedules aclose() as a task."""
    client = _ManagedAsyncClient()
    client._pyleaf_close()

    pending = [t for t in _PENDING_CLOSES if not t.done()]
    assert pending, "aclose() was not scheduled on the running loop"
    await asyncio.gather(*pending)
    assert client.is_closed


def test_web_middleware_named_factory_sets_marker_and_bean_name() -> None:
    """``@WebMiddleware("name")`` is the factory form -> returns ``wrap``."""

    @WebMiddleware("edgeMw")
    class Mw:
        async def dispatch(self, request: Request, call_next: CallNext) -> Response:
            return await call_next(request)

    assert getattr(Mw, WEB_MIDDLEWARE_ATTR) is True
    assert getattr(Mw, COMPONENT_ATTR).name == "edgeMw"


def test_register_middleware_raises_for_bean_without_dispatch() -> None:
    @WebMiddleware
    class NoDispatch:
        pass

    ctx = _ctx(NoDispatch)
    with pytest.raises(TypeError, match="has no callable"):
        register_middleware(FastAPI(), ctx)


def test_join_prepends_slash_when_joined_path_lacks_one() -> None:
    assert _join("api/", "items") == "/api/items"
    assert _join("", "items") == "/items"
    assert _join("/api", "/items") == "/api/items"


def test_response_model_is_forwarded_to_add_api_route() -> None:
    """An explicit ``response_model`` must reach FastAPI (not be dropped)."""

    class Out(BaseModel):
        value: int

    @RestController("/rm")
    class RmController:
        @GetMapping("/thing", response_model=Out)
        def thing(self) -> dict[str, Any]:
            return {"value": 3, "internal": "filtered-out"}

    ctx = _ctx(RmController)
    resp = TestClient(_app(ctx)).get("/rm/thing")

    assert resp.status_code == 200
    # 'internal' was dropped -> the response_model really was applied.
    assert resp.json() == {"value": 3}


def test_controller_advice_named_factory_sets_marker() -> None:
    @ControllerAdvice("namedAdvice")
    class Adv:
        pass

    assert getattr(Adv, CONTROLLER_ADVICE_ATTR) is True


def test_exception_handler_rejects_non_exception_types() -> None:
    with pytest.raises(TypeError, match="exception classes"):
        ExceptionHandler(int)


def test_advice_params_resolve_by_conventional_names() -> None:
    """Unannotated ``request``/``exc`` params resolve by name."""

    @RestController("/fail")
    class Ctl:
        @GetMapping("/key")
        def key(self) -> None:
            raise KeyError("lost")

    @ControllerAdvice
    class Adv:
        @ExceptionHandler(KeyError)
        def by_name(self, request, exc):
            return {"path": request.url.path, "err": type(exc).__name__}

    ctx = _ctx(Ctl, Adv)
    resp = TestClient(_app(ctx)).get("/fail/key")

    assert resp.status_code == 500
    assert resp.json() == {"path": "/fail/key", "err": "KeyError"}


def test_advice_param_falls_back_to_name_when_hints_unresolvable() -> None:
    """A hint get_type_hints can't resolve leaves name-based resolution."""

    @RestController("/fail")
    class Ctl:
        @GetMapping("/value")
        def value(self) -> None:
            raise ValueError("broken")

    @ControllerAdvice
    class Adv:
        @ExceptionHandler(ValueError)
        def bad_hint(self, e: "Unresolvable"):  # noqa: F821
            return {"ok": True}

    ctx = _ctx(Ctl, Adv)
    resp = TestClient(_app(ctx)).get("/fail/value")

    assert resp.status_code == 500
    assert resp.json() == {"ok": True}


def test_register_advice_fails_fast_on_unresolvable_param() -> None:
    @ControllerAdvice
    class Adv:
        @ExceptionHandler(KeyError)
        def h(self, mystery):
            return {}

    ctx = _ctx(Adv)
    with pytest.raises(TypeError, match="mystery"):
        register_advice(FastAPI(), ctx)


def test_sync_handler_returning_awaitable_is_awaited() -> None:
    """A sync handler (e.g. an AOP proxy) returning a coroutine is awaited."""

    @RestController("/coro")
    class Ctl:
        @GetMapping("/v")
        def v(self) -> None:
            raise ValueError("nope")

    @ControllerAdvice
    class Adv:
        @ExceptionHandler(ValueError)
        def h(self, exc: ValueError):
            # Sync signature but returns a coroutine object.
            return self._render(exc)

        async def _render(self, exc: ValueError) -> dict[str, Any]:
            return {"handled": str(exc)}

    ctx = _ctx(Ctl, Adv)
    resp = TestClient(_app(ctx)).get("/coro/v")

    assert resp.status_code == 500
    assert resp.json() == {"handled": "nope"}


def test_exception_after_response_start_reraises_instead_of_dispatch() -> None:
    """Once the response has started, advice cannot rewrite it: re-raise."""

    @RestController("/stream")
    class Ctl:
        @GetMapping("/s")
        def s(self) -> StreamingResponse:
            def gen() -> Iterator[bytes]:
                yield b"chunk"
                raise RuntimeError("mid-stream")

            return StreamingResponse(gen())

    @ControllerAdvice
    class Adv:
        @ExceptionHandler(RuntimeError)
        def h(self) -> dict[str, bool]:
            return {"handled": True}

    ctx = _ctx(Ctl, Adv)
    with pytest.raises(RuntimeError, match="mid-stream"):
        TestClient(_app(ctx)).get("/stream/s")
