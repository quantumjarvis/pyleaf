"""pytester-based tests for ``mock_bean`` / ``spy_bean`` overrides.

Declaration mechanism: tests override the plugin-provided ``pyleaf_beans``
fixture (module- or class-level ``@pytest.fixture``) returning a list of
override descriptors; a ``pyleaf_beans`` class attribute holding a list is
merged in as well.
"""

import pytest

_APP = '''
from pyleaf.core import Component


@Component
class Repo:
    def find(self) -> str:
        return "real"


@Component
class Service:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo

    def run(self) -> str:
        return self.repo.find()
'''


def _write_app(pytester: pytest.Pytester) -> None:
    appdir = pytester.mkpydir("app")
    (appdir / "repo.py").write_text(_APP, encoding="utf-8")


def test_mock_bean_fixture_override(pytester: pytest.Pytester) -> None:
    """mock_bean(T): ctx.get returns a spec'd Mock; dependents get it."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        from unittest.mock import Mock

        import pytest
        from pyleaf.test import PyleafTest, mock_bean
        from app.repo import Repo, Service

        @pytest.fixture
        def pyleaf_beans():
            return [mock_bean(Repo)]

        @PyleafTest(scan="app")
        class TestMock:
            def test_get_returns_specced_mock(self, pyleaf_context):
                repo = pyleaf_context.get(Repo)
                assert isinstance(repo, Mock)

            def test_dependent_sees_configured_mock(self, pyleaf_context):
                pyleaf_context.get(Repo).find.return_value = "fake"
                assert pyleaf_context.get(Service).run() == "fake"
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)


def test_mock_bean_class_attribute(pytester: pytest.Pytester) -> None:
    """A ``pyleaf_beans`` class attribute is merged with the fixture list."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        from unittest.mock import Mock

        from pyleaf.test import PyleafTest, mock_bean
        from app.repo import Repo, Service

        @PyleafTest(scan="app")
        class TestMockAttr:
            pyleaf_beans = [mock_bean(Repo)]

            def test_mock_via_attr(self, pyleaf_context):
                assert isinstance(pyleaf_context.get(Repo), Mock)
                assert isinstance(pyleaf_context.get(Service).repo, Mock)
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_mock_bean_adds_missing_bean(pytester: pytest.Pytester) -> None:
    """mock_bean on an unregistered type registers a new mock bean."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        from unittest.mock import Mock

        import pytest
        from pyleaf.test import PyleafTest, mock_bean

        class Collaborator:
            def act(self) -> str:
                return "real"

        @pytest.fixture
        def pyleaf_beans():
            return [mock_bean(Collaborator)]

        @PyleafTest(scan="app")
        class TestAdd:
            def test_mock_added(self, pyleaf_context):
                assert isinstance(pyleaf_context.get(Collaborator), Mock)
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_spy_bean_wraps_real_instance(pytester: pytest.Pytester) -> None:
    """spy_bean(T): real behavior runs and calls are recorded."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        from unittest.mock import Mock

        import pytest
        from pyleaf.test import PyleafTest, spy_bean
        from app.repo import Repo, Service

        @pytest.fixture
        def pyleaf_beans():
            return [spy_bean(Repo)]

        @PyleafTest(scan="app")
        class TestSpy:
            def test_real_behavior_recorded(self, pyleaf_context):
                repo = pyleaf_context.get(Repo)
                assert isinstance(repo, Mock)
                assert repo.find() == "real"
                repo.find.assert_called_once()

            def test_dependent_calls_through(self, pyleaf_context):
                assert pyleaf_context.get(Service).run() == "real"
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)


def test_override_after_refresh_raises(pytester: pytest.Pytester) -> None:
    """Applying an override to a refreshed context raises PyleafError
    naming the bean."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        import pytest
        from pyleaf.core import PyleafError
        from pyleaf.test import PyleafTest, mock_bean, spy_bean
        from app.repo import Repo

        @PyleafTest(scan="app")
        class TestLate:
            def test_late_mock(self, pyleaf_context):
                with pytest.raises(PyleafError, match="Repo"):
                    mock_bean(Repo).apply(pyleaf_context)

            def test_late_spy(self, pyleaf_context):
                with pytest.raises(PyleafError, match="Repo"):
                    spy_bean(Repo).apply(pyleaf_context)
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)


def test_spy_bean_on_missing_bean_fails(pytester: pytest.Pytester) -> None:
    """spy needs a real bean to wrap; a missing target fails at refresh."""
    _write_app(pytester)
    pytester.makepyfile(
        """
        import pytest
        from pyleaf.test import PyleafTest, spy_bean

        class NotABean:
            pass

        @pytest.fixture
        def pyleaf_beans():
            return [spy_bean(NotABean)]

        @PyleafTest(scan="app")
        class TestSpyMissing:
            def test_fails(self, pyleaf_context):
                pass
        """
    )
    result = pytester.runpytest()
    # fixture setup error surfaces as a failed/error outcome, not a pass
    result.assert_outcomes(passed=0, errors=1)
