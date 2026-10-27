import pytest
from fixture_scanpkg.config import ScannedBean, ScannedConfig
from fixture_scanpkg.excluded_mod import ExcludedService
from fixture_scanpkg.services import ScannedService
from fixture_scanpkg.sub.deep import DeepService
from pyleaf.core import ApplicationContext, NoSuchBean, PyleafError


def test_scan_registers_components() -> None:
    """Test that scanning a package registers its @Component classes."""

    ctx = ApplicationContext(scan=["fixture_scanpkg"])
    ctx.refresh()

    # get_bean hits the registry directly (no auto-register fallback),
    # so it proves the bean was registered by the scan.
    service = ctx.get_bean("scannedService")
    assert isinstance(service, ScannedService)
    assert ctx.get(ScannedService) is service


def test_scan_recurses_into_subpackages() -> None:
    """Test that scanning walks subpackages recursively."""

    ctx = ApplicationContext(scan=["fixture_scanpkg"])
    ctx.refresh()

    deep = ctx.get_bean("deepService")
    assert isinstance(deep, DeepService)
    assert ctx.get(DeepService) is deep


def test_scanned_configuration_bean_method_produces_bean() -> None:
    """Test that a scanned @Configuration's @Bean method yields a bean."""

    ctx = ApplicationContext(scan=["fixture_scanpkg"])
    ctx.refresh()

    config = ctx.get_bean("scannedConfig")
    assert isinstance(config, ScannedConfig)

    # ScannedBean has no stereotype, so only a scanned @Bean registration
    # can satisfy this lookup.
    bean = ctx.get_bean("scanned_bean")
    assert isinstance(bean, ScannedBean)
    assert ctx.get(ScannedBean) is bean


def test_scan_exclude_glob_skips_module() -> None:
    """Test that exclude= fnmatch globs skip matching modules."""

    ctx = ApplicationContext(scan=["fixture_scanpkg"], exclude=["*excluded*"])
    ctx.refresh()

    # Non-excluded modules are still scanned.
    assert isinstance(ctx.get_bean("scannedService"), ScannedService)

    # The excluded module's component was never registered.
    with pytest.raises(NoSuchBean, match=r"excludedService"):
        ctx.get_bean("excludedService")
    assert ctx.get_all(ExcludedService) == []


def test_scan_import_failure_raises_pyleaf_error() -> None:
    """Test that a scanned module that fails to import aborts refresh."""

    ctx = ApplicationContext(scan=["fixture_scanpkg_broken"])
    with pytest.raises(PyleafError, match=r"fixture_scanpkg_broken\.explodes") as exc:
        ctx.refresh()
    assert "kaboom" in str(exc.value)
