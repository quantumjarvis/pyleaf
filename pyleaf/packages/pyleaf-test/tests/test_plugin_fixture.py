"""pytester-based tests for ``@PyleafTest`` + the ``pyleaf_context`` fixture.

Each test writes a small app package plus a test module into an isolated
pytester run; the pyleaf plugin is picked up from its ``pytest11`` entry
point in the inner run.
"""

import pytest

_APP = '''
from pyleaf.core import Component, Environment


@Component
class Greeter:
    def greet(self) -> str:
        return "hello"


@Component
class Settings:
    def __init__(self, environment: Environment) -> None:
        self.key = environment.get("app.key")
        self.profiles = set(environment.active_profiles)
'''


def _write_app(pytester: pytest.Pytester) -> None:
    appdir = pytester.mkpydir("app")
    (appdir / "services.py").write_text(_APP, encoding="utf-8")


def test_pyleaf_context_scans_and_injects(pytester: pytest.Pytester) -> None:
    _write_app(pytester)
    pytester.makepyfile(
        """
        from pyleaf.test import PyleafTest
        from app.services import Greeter, Settings

        @PyleafTest(scan="app", profiles=["test"], properties={"app.key": "meta"})
        class TestApp:
            def test_ctx(self, pyleaf_context):
                assert pyleaf_context.get(Greeter).greet() == "hello"
                settings = pyleaf_context.get(Settings)
                assert settings.key == "meta"
                assert settings.profiles == {"test"}
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_pyleaf_context_on_a_function(pytester: pytest.Pytester) -> None:
    _write_app(pytester)
    pytester.makepyfile(
        """
        from pyleaf.test import PyleafTest
        from app.services import Greeter

        @PyleafTest(scan="app")
        def test_fn(pyleaf_context):
            assert pyleaf_context.get(Greeter).greet() == "hello"
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_properties_dict_wins_over_config_dir(pytester: pytest.Pytester) -> None:
    """PyleafTest properties layer last: they beat application.yaml values,
    while yaml-only keys still resolve."""
    _write_app(pytester)
    pytester.makefile(".yaml", **{"conf/application": "app:\n  key: yaml\nother: 7\n"})
    pytester.makepyfile(
        """
        from pathlib import Path

        from pyleaf.test import PyleafTest
        from app.services import Settings

        CONF = str(Path(__file__).parent / "conf")

        @PyleafTest(scan="app", config_dir=CONF, properties={"app.key": "meta"})
        class TestApp:
            def test_props_win(self, pyleaf_context):
                assert pyleaf_context.get(Settings).key == "meta"
                assert pyleaf_context.environment.get("other") == 7
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_config_dir_profile_yaml(pytester: pytest.Pytester) -> None:
    """Profile-specific yaml wins over the base file; profiles propagate."""
    _write_app(pytester)
    pytester.makefile(
        ".yaml",
        **{
            "conf/application": "app:\n  key: base\n",
            "conf/application-test": "app:\n  key: profiled\n",
        },
    )
    pytester.makepyfile(
        """
        from pathlib import Path

        from pyleaf.test import PyleafTest
        from app.services import Settings

        CONF = str(Path(__file__).parent / "conf")

        @PyleafTest(scan="app", profiles=["test"], config_dir=CONF)
        class TestApp:
            def test_profile_yaml(self, pyleaf_context):
                settings = pyleaf_context.get(Settings)
                assert settings.key == "profiled"
                assert settings.profiles == {"test"}
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_context_is_closed_on_teardown(pytester: pytest.Pytester) -> None:
    """@PreDestroy hooks run when the fixture tears the context down."""
    appdir = pytester.mkpydir("app")
    (appdir / "services.py").write_text(
        '''
from pyleaf.core import Component, PreDestroy


@Component
class Closer:
    def __init__(self) -> None:
        self.closed = False

    @PreDestroy
    def shutdown(self) -> None:
        self.closed = True
''',
        encoding="utf-8",
    )
    pytester.makepyfile(
        """
        from pyleaf.test import PyleafTest
        from app.services import Closer

        seen = {}

        @PyleafTest(scan="app")
        class TestApp:
            def test_grab_ctx(self, pyleaf_context):
                seen["bean"] = pyleaf_context.get(Closer)
                assert not seen["bean"].closed

        def test_after(pyleaf_context):
            # Function-scoped: a fresh context here; the previous one closed.
            assert seen["bean"].closed
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)
