"""M1 benchmark: package scanning over a faked 500-module package.

``pkgutil.walk_packages`` and ``importlib.import_module`` are mocked so the
scan exercises real registration work without filesystem/import overhead.
"""

import importlib
import pkgutil
from types import ModuleType
from unittest.mock import patch

from benchgen import make_component
from pyleaf.core import ApplicationContext

MODULE_COUNT = 500
THRESHOLD_S = 0.500
PKG_NAME = "fakepkg"


def _fake_package() -> tuple[dict[str, ModuleType], list[pkgutil.ModuleInfo]]:
    modules: dict[str, ModuleType] = {}
    infos: list[pkgutil.ModuleInfo] = []

    pkg = ModuleType(PKG_NAME)
    pkg.__path__ = []  # type: ignore[attr-defined]
    modules[PKG_NAME] = pkg

    for i in range(MODULE_COUNT):
        name = f"{PKG_NAME}.mod{i}"
        module = ModuleType(name)
        cls = make_component(f"Scanned{i}")
        setattr(module, f"Scanned{i}", cls)
        modules[name] = module
        infos.append(pkgutil.ModuleInfo(None, name, False))  # type: ignore[arg-type]
    return modules, infos


MODULES, INFOS = _fake_package()


def _fake_import(name: str, package: str | None = None) -> ModuleType:
    return MODULES[name]


def _run() -> ApplicationContext:
    ctx = ApplicationContext(scan=[PKG_NAME])
    ctx.refresh()
    return ctx


def test_scan_500_module_package(benchmark) -> None:
    with (
        patch.object(importlib, "import_module", side_effect=_fake_import),
        patch.object(
            pkgutil, "walk_packages", side_effect=lambda *a, **k: iter(INFOS)
        ),
    ):
        ctx = benchmark(_run)

    assert ctx.get_bean("scanned0") is not None
    assert ctx.get_bean(f"scanned{MODULE_COUNT - 1}") is not None
    assert benchmark.stats["median"] < THRESHOLD_S
