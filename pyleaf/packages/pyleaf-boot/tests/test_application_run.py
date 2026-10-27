"""End-to-end ``PyleafApplication.run``: env, scan, autoconfig, runners, banner."""

import importlib
from pathlib import Path

import pytest
from pyleaf.boot import PyleafApplication
from pyleaf.core import ApplicationContext

_SHARED_MODULE = """\
class Token:
    pass
"""

_APP_PKG = """\
from pyleaf.core import Component, Order
from shared_mod import Token

calls = []


@Component
class UserToken(Token):
    pass


@Order(1)
@Component
class FirstRunner:
    def run(self, *args):
        calls.append(("first", args))


@Order(2)
@Component
class SecondRunner:
    def run(self, args):
        calls.append(("second", args))
"""

_AUTO_MODULE = """\
from pyleaf.boot import AutoConfiguration
from pyleaf.core import Bean, ConditionalOnMissingBean
from shared_mod import Token


@AutoConfiguration
class FallbackAutoConfig:
    @Bean
    @ConditionalOnMissingBean(Token)
    def fallback_token(self) -> Token:
        return Token()
"""

_ENTRY_POINTS = (
    "[pyleaf.autoconfigure]\nfallback-auto = fake_auto_mod:FallbackAutoConfig\n"
)


def _make_app_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A scannable package + shared type + fake dist-info under tmp_path."""
    (tmp_path / "shared_mod.py").write_text(_SHARED_MODULE, encoding="utf-8")
    (tmp_path / "fake_auto_mod.py").write_text(_AUTO_MODULE, encoding="utf-8")
    pkg = tmp_path / "apppkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(_APP_PKG, encoding="utf-8")
    dist = tmp_path / "fakepkg-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fakepkg\nVersion: 0.1\n", encoding="utf-8"
    )
    (dist / "entry_points.txt").write_text(_ENTRY_POINTS, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()


def test_run_returns_refreshed_ctx_with_env_and_beans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_app_tree(tmp_path, monkeypatch)
    (tmp_path / "application.yaml").write_text(
        "app:\n  greeting: hello\n", encoding="utf-8"
    )

    ctx = PyleafApplication.run("apppkg", (), config_dir=tmp_path, banner=False)

    assert isinstance(ctx, ApplicationContext)
    # Environment was loaded from the config dir.
    assert ctx.environment.get("app.greeting") == "hello"


def test_run_scans_and_applies_autoconfigs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user's Token bean satisfies the auto-config's OnMissingBean."""
    _make_app_tree(tmp_path, monkeypatch)

    ctx = PyleafApplication.run("apppkg", (), config_dir=tmp_path, banner=False)

    apppkg = importlib.import_module("apppkg")
    shared_mod = importlib.import_module("shared_mod")
    token = ctx.get(shared_mod.Token)
    # Scanned user bean wins over the entry-point auto-configuration.
    assert isinstance(token, apppkg.UserToken)


def test_run_invokes_runners_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_app_tree(tmp_path, monkeypatch)
    # The apppkg module persists in sys.modules between tests; reset its
    # recorder so only this run's invocations are asserted.
    apppkg = importlib.import_module("apppkg")
    apppkg.calls.clear()

    PyleafApplication.run(
        "apppkg", ("--x=1",), config_dir=tmp_path, banner=False
    )

    assert apppkg.calls == [
        ("first", ("--x=1",)),
        ("second", ("--x=1",)),
    ]


def test_banner_prints_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_app_tree(tmp_path, monkeypatch)

    PyleafApplication.run("apppkg", (), config_dir=tmp_path, banner=True)

    out = capsys.readouterr().out
    assert "pyleaf" in out
    assert "apppkg" in out


def test_banner_suppressed_by_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_app_tree(tmp_path, monkeypatch)

    PyleafApplication.run("apppkg", (), config_dir=tmp_path, banner=False)

    assert capsys.readouterr().out == ""


def test_banner_suppressed_by_property(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_app_tree(tmp_path, monkeypatch)

    PyleafApplication.run(
        "apppkg", ("--pyleaf.banner=false",), config_dir=tmp_path, banner=True
    )

    assert "pyleaf" not in capsys.readouterr().out


def test_run_is_repeatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_app_tree(tmp_path, monkeypatch)

    ctx1 = PyleafApplication.run("apppkg", (), config_dir=tmp_path, banner=False)
    ctx2 = PyleafApplication.run("apppkg", (), config_dir=tmp_path, banner=False)

    assert ctx1 is not ctx2
