"""pytester-based tests for the ``pyleaf_client`` web fixture.

The fixture builds a per-test FastAPI app over ``pyleaf_context`` with the
same wiring ``fastapiApp`` produces and wraps it in a ``TestClient``;
``mock_bean``/``spy_bean`` overrides applied pre-refresh are what the
controllers serve.
"""

import pytest

_APP = '''
import uuid

from pyleaf.core import Component, Scope
from pyleaf.web import (
    ControllerAdvice,
    ExceptionHandler,
    GetMapping,
    PostMapping,
    RestController,
)


class BoomError(Exception):
    """Raised by /api/boom; handled by ApiAdvice."""


@Component
class GreetingService:
    def greet(self) -> str:
        return "real"


@Component(scope=Scope.REQUEST)
class RequestId:
    def __init__(self) -> None:
        self.ident = uuid.uuid4().hex


@RestController("/api")
class ApiController:
    def __init__(
        self, service: GreetingService, rid: RequestId
    ) -> None:
        self.service = service
        self.request_id = rid

    @GetMapping("/greet")
    def greet(self) -> dict[str, str]:
        return {"msg": self.service.greet()}

    @PostMapping("/echo")
    async def echo(self, body: dict[str, str]) -> dict[str, str]:
        return {"echo": body["msg"].upper()}

    @GetMapping("/rid")
    def rid(self) -> dict[str, str]:
        return {"id": self.request_id.ident}

    @GetMapping("/boom")
    def boom(self) -> None:
        raise BoomError("kaboom")


@ControllerAdvice
class ApiAdvice:
    @ExceptionHandler(BoomError, status_code=418)
    def on_boom(self, exc: BoomError) -> dict[str, str]:
        return {"handled": str(exc)}
'''


def _write_app(pytester: pytest.Pytester) -> None:
    appdir = pytester.mkpydir("app")
    (appdir / "api.py").write_text(_APP, encoding="utf-8")


def test_client_get_and_post_roundtrip(pytester: pytest.Pytester) -> None:
    """GET and POST hit a DI'd @RestController through the client."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        from pyleaf.test import PyleafTest

        @PyleafTest(scan="app")
        class TestRoutes:
            def test_get(self, pyleaf_client):
                resp = pyleaf_client.get("/api/greet")
                assert resp.status_code == 200
                assert resp.json() == {"msg": "real"}

            def test_post_async_endpoint(self, pyleaf_client):
                resp = pyleaf_client.post("/api/echo", json={"msg": "hi"})
                assert resp.status_code == 200
                assert resp.json() == {"echo": "HI"}
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)


def test_mock_bean_is_served_through_client(pytester: pytest.Pytester) -> None:
    """mock_bean(T) applied pre-refresh is what the controller serves."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        import pytest
        from pyleaf.test import PyleafTest, mock_bean
        from app.api import GreetingService

        @pytest.fixture
        def pyleaf_beans():
            return [mock_bean(GreetingService)]

        @PyleafTest(scan="app")
        class TestMockServed:
            def test_response_shows_mock_value(
                self, pyleaf_context, pyleaf_client
            ):
                pyleaf_context.get(GreetingService).greet.return_value = "fake"
                resp = pyleaf_client.get("/api/greet")
                assert resp.status_code == 200
                assert resp.json() == {"msg": "fake"}
                pyleaf_context.get(GreetingService).greet.assert_called_once_with()

            def test_fresh_mock_next_test(self, pyleaf_context):
                # The previous test's configured mock did not leak over.
                assert pyleaf_context.get(GreetingService).greet.call_count == 0
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)


def test_request_scope_gets_a_scope_per_request(
    pytester: pytest.Pytester,
) -> None:
    """A Scope.REQUEST bean resolves live per request through the client."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        from pyleaf.test import PyleafTest

        @PyleafTest(scan="app")
        class TestRequestScope:
            def test_distinct_instances_across_requests(self, pyleaf_client):
                first = pyleaf_client.get("/api/rid")
                second = pyleaf_client.get("/api/rid")
                assert first.status_code == 200
                assert second.status_code == 200
                assert first.json()["id"] != second.json()["id"]
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_controller_advice_handles_through_client(
    pytester: pytest.Pytester,
) -> None:
    """@ExceptionHandler beans produce the response, not a 500."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        from pyleaf.test import PyleafTest

        @PyleafTest(scan="app")
        class TestAdvice:
            def test_boom_handled(self, pyleaf_client):
                resp = pyleaf_client.get("/api/boom")
                assert resp.status_code == 418
                assert resp.json() == {"handled": "kaboom"}
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_fresh_app_per_test_no_cross_talk(pytester: pytest.Pytester) -> None:
    """Each test gets its own ctx + app: mutations never leak across."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        import pytest
        from pyleaf.test import PyleafTest
        from app.api import ApiController

        @PyleafTest(scan="app")
        class TestIsolation:
            def test_one_uses_client(self, pyleaf_context, pyleaf_client):
                assert pyleaf_client.get("/api/greet").status_code == 200
                # Same singleton controller, but a different app instance
                # would not reset anything — the ctx does that per test.
                assert pyleaf_context.get(ApiController) is not None

            def test_two_sees_fresh_context(
                self, pyleaf_context, pyleaf_client
            ):
                assert pyleaf_client.get("/api/greet").json() == {"msg": "real"}
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)


def test_client_skips_when_web_stack_missing(
    pytester: pytest.Pytester,
) -> None:
    """Absent fastapi/pyleaf.web -> skip, not an import-time failure."""
    pytester.makepyfile(
        """
        import sys

        # Simulate a web-stack-less environment: a None entry in
        # sys.modules makes ``from fastapi import ...`` raise ImportError.
        sys.modules["fastapi"] = None

        from pyleaf.test import PyleafTest

        @PyleafTest
        class TestNoWeb:
            def test_skips(self, pyleaf_client):
                raise AssertionError("fixture should have skipped")
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(skipped=1)
