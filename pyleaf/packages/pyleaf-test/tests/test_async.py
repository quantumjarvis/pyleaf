"""pytest-asyncio compatibility: async tests can await + use ctx.get."""

import pytest

_APP = '''
from pyleaf.core import Component


@Component
class Greeter:
    def greet(self) -> str:
        return "hello"
'''


def test_async_test_uses_pyleaf_context(pytester: pytest.Pytester) -> None:
    appdir = pytester.mkpydir("app")
    (appdir / "services.py").write_text(_APP, encoding="utf-8")
    pytester.makepyfile(
        """
        import pytest
        from pyleaf.test import PyleafTest
        from app.services import Greeter

        @pytest.mark.asyncio
        @PyleafTest(scan="app")
        async def test_async_ctx(pyleaf_context):
            assert pyleaf_context.get(Greeter).greet() == "hello"
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
