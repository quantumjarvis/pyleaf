"""Deferred bean overrides: ``ApplicationContext.override_bean``.

Overrides queue pre-refresh, apply inside ``refresh()`` right after package
scanning (so scanned beans are overridable too), and raise ``PyleafError``
naming the bean when attempted on an already-refreshed context.
"""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from pyleaf.core import (
    ApplicationContext,
    BeanDefinition,
    Component,
    NoUniqueBean,
    PyleafError,
)


@Component
class Repo:
    def find(self) -> str:
        return "real"


@Component
class OtherRepo(Repo):
    pass


@Component
class Service:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo

    def run(self) -> str:
        return self.repo.find()


def _mock_transform(old: BeanDefinition | None) -> BeanDefinition:
    assert old is not None
    return replace(old, factory=lambda: Mock(spec=Repo), dep_specs=lambda: [])


def test_override_replaces_factory_pre_refresh() -> None:
    ctx = ApplicationContext()
    ctx.register(Repo)
    ctx.override_bean(Repo, _mock_transform)
    ctx.refresh()
    assert isinstance(ctx.get(Repo), Mock)


def test_overridden_bean_is_injected_into_dependents() -> None:
    ctx = ApplicationContext()
    ctx.register(Repo)
    ctx.register(Service)
    ctx.override_bean(Repo, _mock_transform)
    ctx.refresh()
    repo = ctx.get(Repo)
    repo.find.return_value = "fake"
    assert ctx.get(Service).run() == "fake"


def test_override_by_bean_name() -> None:
    ctx = ApplicationContext()
    ctx.register(Repo)
    ctx.override_bean("repo", _mock_transform)
    ctx.refresh()
    assert isinstance(ctx.get_bean("repo"), Mock)


def test_override_transform_gets_none_for_missing_bean() -> None:
    """A transform may add a bean when the target was never registered."""

    def add_transform(old: BeanDefinition | None) -> BeanDefinition:
        assert old is None
        return BeanDefinition(
            name="added", bean_type=dict, factory=dict, primary=True
        )

    ctx = ApplicationContext()
    ctx.override_bean(dict, add_transform)
    ctx.refresh()
    assert ctx.get(dict) == {}


def test_override_applies_after_scan() -> None:
    """Scanned definitions exist by the time queued overrides run."""
    ctx = ApplicationContext(scan=["fixture_scanpkg"])
    ctx.override_bean(
        "scannedService",
        lambda old: replace(old, factory=lambda: "overridden"),
    )
    ctx.refresh()
    assert ctx.get_bean("scannedService") == "overridden"


def test_override_after_refresh_raises_naming_the_bean() -> None:
    ctx = ApplicationContext()
    ctx.register(Repo)
    ctx.refresh()
    with pytest.raises(PyleafError, match="Repo"):
        ctx.override_bean(Repo, _mock_transform)


def test_override_ambiguous_type_raises() -> None:
    """Multiple candidates with no primary fail at refresh, when queued
    overrides are applied."""
    ctx = ApplicationContext()
    ctx.register(Repo)
    ctx.register(OtherRepo)
    ctx.override_bean(Repo, _mock_transform)
    with pytest.raises(NoUniqueBean):
        ctx.refresh()


def test_override_keeps_the_original_name_slot() -> None:
    """A transform returning a differently named def keeps the old name."""

    def rename_transform(old: BeanDefinition | None) -> BeanDefinition:
        assert old is not None
        return BeanDefinition(
            name="renamed",
            bean_type=Repo,
            factory=lambda: Mock(spec=Repo),
        )

    ctx = ApplicationContext()
    ctx.register(Repo)
    ctx.register(Service)
    ctx.override_bean(Repo, rename_transform)
    ctx.refresh()
    # Injected under the original name: dependents still resolve it.
    assert isinstance(ctx.get(Service).repo, Mock)
    assert isinstance(ctx.get_bean("repo"), Mock)

