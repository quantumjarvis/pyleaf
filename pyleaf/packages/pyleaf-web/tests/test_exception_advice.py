"""@ControllerAdvice/@ExceptionHandler: specificity, @Order, returns, DI."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pyleaf.core import ApplicationContext, Order, Service
from pyleaf.web import (
    ControllerAdvice,
    ExceptionHandler,
    GetMapping,
    RestController,
    register_advice,
    register_controllers,
)


class DomainError(Exception):
    """Base application error."""


class NotFoundError(DomainError):
    """More specific application error."""


class UnrelatedError(Exception):
    """No advice covers this one."""


def _ctx(*classes: type) -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def _client(app: FastAPI) -> TestClient:
    # raise_server_exceptions=False so the unhandled->500 path is assertable;
    # handled exceptions never re-raise either way.
    return TestClient(app, raise_server_exceptions=False)


def test_most_specific_wins_within_one_advice() -> None:
    @ControllerAdvice
    class Advice:
        @ExceptionHandler(Exception)
        def generic(self, exc: Exception) -> dict[str, str]:
            return {"handler": "generic"}

        @ExceptionHandler(DomainError, status_code=422)
        def domain(self, exc: DomainError) -> dict[str, str]:
            return {"handler": "domain"}

        @ExceptionHandler(NotFoundError, status_code=404)
        def not_found(self, exc: NotFoundError) -> dict[str, str]:
            return {"handler": "not_found"}

    app = FastAPI()

    @app.get("/not-found")
    def nf() -> None:
        raise NotFoundError("gone")

    @app.get("/domain")
    def dm() -> None:
        raise DomainError("bad")

    @app.get("/other")
    def ot() -> None:
        raise UnrelatedError("misc")

    register_advice(app, _ctx(Advice))
    client = _client(app)

    resp = client.get("/not-found")
    assert resp.status_code == 404
    assert resp.json() == {"handler": "not_found"}

    resp = client.get("/domain")
    assert resp.status_code == 422
    assert resp.json() == {"handler": "domain"}

    resp = client.get("/other")
    assert resp.status_code == 500
    assert resp.json() == {"handler": "generic"}


def test_specificity_beats_order_across_advice_beans() -> None:
    """A more specific declared type wins even from a lower-@Order bean."""

    @ControllerAdvice
    @Order(1)
    class BaseAdvice:
        @ExceptionHandler(DomainError, status_code=409)
        def handle(self, exc: DomainError) -> dict[str, str]:
            return {"handler": "base"}

    @ControllerAdvice
    @Order(99)
    class SpecificAdvice:
        @ExceptionHandler(NotFoundError, status_code=404)
        def handle(self, exc: NotFoundError) -> dict[str, str]:
            return {"handler": "specific"}

    app = FastAPI()

    @app.get("/")
    def boom() -> None:
        raise NotFoundError("x")

    register_advice(app, _ctx(BaseAdvice, SpecificAdvice))
    resp = _client(app).get("/")
    assert resp.status_code == 404
    assert resp.json() == {"handler": "specific"}


def test_order_breaks_tie_at_equal_specificity() -> None:
    """Two beans declaring the same type: lowest @Order wins."""

    @ControllerAdvice
    @Order(9)
    class LateAdvice:
        @ExceptionHandler(DomainError, status_code=410)
        def handle(self, exc: DomainError) -> dict[str, str]:
            return {"handler": "late"}

    @ControllerAdvice
    @Order(1)
    class EarlyAdvice:
        @ExceptionHandler(DomainError, status_code=409)
        def handle(self, exc: DomainError) -> dict[str, str]:
            return {"handler": "early"}

    app = FastAPI()

    @app.get("/")
    def boom() -> None:
        raise DomainError("x")

    # Deliberately register the lower-precedence bean first.
    register_advice(app, _ctx(LateAdvice, EarlyAdvice))
    resp = _client(app).get("/")
    assert resp.status_code == 409
    assert resp.json() == {"handler": "early"}


def test_declaration_order_within_one_class() -> None:
    """Same bean, two handlers of equal specificity: first declared wins."""

    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError, status_code=400)
        def first(self, exc: DomainError) -> dict[str, str]:
            return {"handler": "first"}

        @ExceptionHandler(DomainError, status_code=402)
        def second(self, exc: DomainError) -> dict[str, str]:
            return {"handler": "second"}

    app = FastAPI()

    @app.get("/")
    def boom() -> None:
        raise DomainError("x")

    register_advice(app, _ctx(Advice))
    resp = _client(app).get("/")
    assert resp.status_code == 400
    assert resp.json() == {"handler": "first"}


def test_sync_and_async_handlers_both_run() -> None:
    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError, status_code=400)
        def sync_handler(self, exc: DomainError) -> dict[str, str]:
            return {"mode": "sync"}

        @ExceptionHandler(NotFoundError, status_code=404)
        async def async_handler(self, exc: NotFoundError) -> dict[str, str]:
            return {"mode": "async"}

    app = FastAPI()

    @app.get("/sync")
    def s() -> None:
        raise DomainError("x")

    @app.get("/async")
    def a() -> None:
        raise NotFoundError("x")

    register_advice(app, _ctx(Advice))
    client = _client(app)
    assert client.get("/sync").json() == {"mode": "sync"}
    assert client.get("/async").json() == {"mode": "async"}


def test_handler_receives_request_and_exc() -> None:
    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError, status_code=400)
        async def handle(self, request: Request, exc: DomainError) -> dict[str, str]:
            return {"path": request.url.path, "detail": str(exc)}

    app = FastAPI()

    @app.get("/thing")
    def boom() -> None:
        raise DomainError("nope")

    register_advice(app, _ctx(Advice))
    resp = _client(app).get("/thing")
    assert resp.status_code == 400
    assert resp.json() == {"path": "/thing", "detail": "nope"}


def test_response_return_used_directly() -> None:
    """A Response return bypasses the decorator's status_code."""

    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError)  # default 500 must NOT apply
        def handle(self, exc: DomainError) -> JSONResponse:
            return JSONResponse({"custom": True}, status_code=418)

    app = FastAPI()

    @app.get("/")
    def boom() -> None:
        raise DomainError("x")

    register_advice(app, _ctx(Advice))
    resp = _client(app).get("/")
    assert resp.status_code == 418
    assert resp.json() == {"custom": True}


def test_dict_default_status_is_500() -> None:
    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError)
        def handle(self, exc: DomainError) -> dict[str, str]:
            return {"detail": "handled"}

    app = FastAPI()

    @app.get("/")
    def boom() -> None:
        raise DomainError("x")

    register_advice(app, _ctx(Advice))
    resp = _client(app).get("/")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"detail": "handled"}


def test_pydantic_model_return_becomes_json() -> None:
    class Body(BaseModel):
        detail: str

    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError, status_code=400)
        def handle(self, exc: DomainError) -> Body:
            return Body(detail=f"err:{exc}")

    app = FastAPI()

    @app.get("/")
    def boom() -> None:
        raise DomainError("x")

    register_advice(app, _ctx(Advice))
    resp = _client(app).get("/")
    assert resp.status_code == 400
    assert resp.json() == {"detail": "err:x"}


def test_multiple_exc_types_on_one_handler() -> None:
    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError, KeyError, status_code=400)
        def handle(self, exc: Exception) -> dict[str, str]:
            return {"caught": type(exc).__name__}

    app = FastAPI()

    @app.get("/domain")
    def d() -> None:
        raise DomainError("x")

    @app.get("/key")
    def k() -> None:
        raise KeyError("y")

    register_advice(app, _ctx(Advice))
    client = _client(app)
    assert client.get("/domain").json() == {"caught": "DomainError"}
    assert client.get("/key").json() == {"caught": "KeyError"}


def test_unhandled_exception_falls_through_to_500() -> None:
    """No declared handler covers the exception -> default 500."""

    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError, status_code=400)
        def handle(self, exc: DomainError) -> dict[str, str]:
            return {"handler": "domain"}

    app = FastAPI()

    @app.get("/")
    def boom() -> None:
        raise UnrelatedError("x")

    register_advice(app, _ctx(Advice))
    resp = _client(app).get("/")
    assert resp.status_code == 500
    assert resp.text == "Internal Server Error"


def test_advice_bean_supports_constructor_di() -> None:
    @Service
    class Prefix:
        value = "ERR:"

    @ControllerAdvice
    class Advice:
        def __init__(self, prefix: Prefix) -> None:
            self.prefix = prefix

        @ExceptionHandler(DomainError, status_code=400)
        def handle(self, exc: DomainError) -> dict[str, str]:
            return {"msg": self.prefix.value + str(exc)}

    app = FastAPI()

    @app.get("/")
    def boom() -> None:
        raise DomainError("boom")

    register_advice(app, _ctx(Prefix, Advice))
    resp = _client(app).get("/")
    assert resp.status_code == 400
    assert resp.json() == {"msg": "ERR:boom"}


def test_advice_covers_rest_controller_routes() -> None:
    @RestController("/api")
    class Api:
        @GetMapping("/boom")
        def boom(self) -> dict[str, str]:
            raise DomainError("ctrl")

    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError, status_code=400)
        def handle(self, exc: DomainError) -> dict[str, str]:
            return {"msg": str(exc)}

    ctx = _ctx(Api, Advice)
    app = FastAPI()
    register_controllers(app, ctx)
    register_advice(app, ctx)
    resp = _client(app).get("/api/boom")
    assert resp.status_code == 400
    assert resp.json() == {"msg": "ctrl"}


def test_fastapi_http_exception_handling_is_preserved() -> None:
    """Starlette/FastAPI's own handlers (inner middleware) still win."""

    from fastapi import HTTPException

    @ControllerAdvice
    class Advice:
        @ExceptionHandler(DomainError, status_code=400)
        def handle(self, exc: DomainError) -> dict[str, str]:
            return {"handler": "domain"}

    app = FastAPI()

    @app.get("/")
    def boom() -> None:
        raise HTTPException(status_code=403, detail="forbidden")

    register_advice(app, _ctx(Advice))
    resp = _client(app).get("/")
    assert resp.status_code == 403
    assert resp.json() == {"detail": "forbidden"}


def test_register_advice_without_beans_is_noop() -> None:
    app = FastAPI()

    @app.get("/")
    def ok() -> dict[str, bool]:
        return {"ok": True}

    register_advice(app, _ctx())
    assert _client(app).get("/").json() == {"ok": True}


def test_exception_handler_requires_a_type() -> None:
    import pytest

    with pytest.raises(ValueError):
        ExceptionHandler()
