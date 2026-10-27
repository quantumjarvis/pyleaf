"""Entry-point discovery, exclusion and failure reporting for auto-configs."""

import importlib
from pathlib import Path

import pytest
from pyleaf.boot import discover_autoconfigs, register_autoconfigs
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Configuration,
    Environment,
    NoSuchBean,
    PropertySource,
)

_FAKE_MODULE = '''
from pyleaf.boot import AutoConfiguration
from pyleaf.core import Bean, ConditionalOnMissingBean


class FakeService:
    def __init__(self, who="auto"):
        self.who = who


@AutoConfiguration
class FakeAutoConfig:
    @Bean
    @ConditionalOnMissingBean(FakeService)
    def fake_service(self) -> FakeService:
        return FakeService()
'''

_ENTRY_POINTS = (
    "[pyleaf.autoconfigure]\n"
    "fake-auto = fakepkg_mod:FakeAutoConfig\n"
    "broken-auto = fakepkg_mod:NoSuchAttr\n"
    "gone-auto = nonexistent_mod_xyz:Thing\n"
)


@pytest.fixture
def ep_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp dir on sys.path with a fake dist-info + importable module."""
    (tmp_path / "fakepkg_mod.py").write_text(_FAKE_MODULE, encoding="utf-8")
    dist = tmp_path / "fakepkg-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fakepkg\nVersion: 0.1\n", encoding="utf-8"
    )
    (dist / "entry_points.txt").write_text(_ENTRY_POINTS, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    return tmp_path


def _ctx(values: dict[str, object] | None = None) -> ApplicationContext:
    return ApplicationContext(
        environment=Environment(sources=[PropertySource("test", values or {})])
    )


def test_discover_returns_classes_and_captured_failures(ep_tree: Path) -> None:
    import fakepkg_mod

    discovered = discover_autoconfigs()

    assert discovered["fake-auto"] is fakepkg_mod.FakeAutoConfig
    # Broken entry points are captured, never raised.
    assert isinstance(discovered["broken-auto"], Exception)
    assert isinstance(discovered["gone-auto"], Exception)


def test_register_registers_beans_and_reports_failures(ep_tree: Path) -> None:
    import fakepkg_mod

    ctx = _ctx()
    register_autoconfigs(ctx, discover_autoconfigs())
    ctx.refresh()

    assert isinstance(ctx.get(fakepkg_mod.FakeService), fakepkg_mod.FakeService)
    broken = [
        entry
        for entry in ctx.condition_report.entries
        if entry.bean_name in {"broken-auto", "gone-auto"}
    ]
    assert len(broken) == 2
    assert all(not entry.matched for entry in broken)
    assert all(
        any("load failed" in reason for reason in entry.reasons)
        for entry in broken
    )


def test_explicit_exclude_drops_autoconfig(ep_tree: Path) -> None:
    import fakepkg_mod

    ctx = _ctx()
    register_autoconfigs(ctx, discover_autoconfigs(), exclude=["fake-auto"])
    ctx.refresh()

    with pytest.raises(NoSuchBean):
        ctx.get(fakepkg_mod.FakeService)


def test_exclude_matches_dotted_class_name(ep_tree: Path) -> None:
    import fakepkg_mod

    ctx = _ctx()
    register_autoconfigs(
        ctx,
        discover_autoconfigs(),
        exclude=["fakepkg_mod.FakeAutoConfig"],
    )
    ctx.refresh()

    with pytest.raises(NoSuchBean):
        ctx.get(fakepkg_mod.FakeService)


def test_env_property_exclude_drops_autoconfig(ep_tree: Path) -> None:
    import fakepkg_mod

    ctx = _ctx({"pyleaf.autoconfigure.exclude": "fake-auto"})
    register_autoconfigs(ctx, discover_autoconfigs())
    ctx.refresh()

    with pytest.raises(NoSuchBean):
        ctx.get(fakepkg_mod.FakeService)


def test_user_bean_satisfies_conditional_on_missing(ep_tree: Path) -> None:
    """Auto-configs register after user beans, so a user bean wins."""
    import fakepkg_mod

    @Configuration
    class UserConfig:
        @Bean
        def user_service(self) -> fakepkg_mod.FakeService:
            return fakepkg_mod.FakeService(who="user")

    ctx = _ctx()
    ctx.register(UserConfig)
    register_autoconfigs(ctx, discover_autoconfigs())
    ctx.refresh()

    service = ctx.get(fakepkg_mod.FakeService)
    assert service.who == "user"
