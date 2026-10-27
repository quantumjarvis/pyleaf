"""Package scanning: import modules recursively and register marked classes."""

from __future__ import annotations

import fnmatch
import importlib
import pkgutil
import sys
from collections.abc import Iterable, Sequence
from types import ModuleType
from typing import TYPE_CHECKING, Any

from .errors import PyleafError
from .stereotypes import (
    COMPONENT_ATTR,
    CONFIG_PROPS_ATTR,
    CONFIGURATION_ATTR,
)

if TYPE_CHECKING:
    from .context import ApplicationContext


def _is_excluded(dotted_name: str, exclude: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(dotted_name, glob) for glob in exclude)


def _import_module(dotted_name: str) -> ModuleType:
    try:
        return importlib.import_module(dotted_name)
    except Exception as exc:
        raise PyleafError(
            f"Failed to import module '{dotted_name}' during scan:"
            f" {type(exc).__name__}: {exc}"
        ) from exc


def _walk_error(dotted_name: str) -> None:
    """``pkgutil.walk_packages`` onerror hook; re-raise as PyleafError."""
    exc = sys.exc_info()[1]
    raise PyleafError(
        f"Failed to import module '{dotted_name}' during scan:"
        f" {type(exc).__name__}: {exc}"
    ) from exc


def _is_stereotype(value: Any) -> bool:
    return isinstance(value, type) and (
        getattr(value, COMPONENT_ATTR, None) is not None
        or getattr(value, CONFIGURATION_ATTR, None) is not None
        or getattr(value, CONFIG_PROPS_ATTR, None) is not None
    )


def _register_module_classes(
    ctx: ApplicationContext, module: ModuleType, seen: set[int]
) -> list[type[Any]]:
    """Register every marker-attr class visible in ``module``'s namespace."""
    registered: list[type[Any]] = []
    for value in vars(module).values():
        if not _is_stereotype(value):
            continue
        if id(value) in seen:
            # Same class object re-exported from another scanned module
            # (or registered before the scan): deduplicate silently.
            continue
        seen.add(id(value))
        ctx.register(value)
        registered.append(value)
    return registered


def _walk_module_names(pkg: ModuleType, exclude: Sequence[str]) -> Iterable[str]:
    """Yield importable dotted module names under ``pkg``, honoring excludes.

    ``pkgutil.walk_packages`` recurses into subpackages itself; a package
    whose dotted name matches an exclude glob is skipped together with its
    whole subtree.
    """
    path = getattr(pkg, "__path__", None)
    if path is None:
        return
    skipped_prefixes: list[str] = []
    for info in pkgutil.walk_packages(
        path, prefix=f"{pkg.__name__}.", onerror=_walk_error
    ):
        if any(info.name.startswith(prefix) for prefix in skipped_prefixes):
            continue
        if _is_excluded(info.name, exclude):
            if info.ispkg:
                skipped_prefixes.append(info.name + ".")
            continue
        yield info.name


def scan_packages(
    ctx: ApplicationContext,
    packages: Sequence[str],
    exclude: Sequence[str] = (),
) -> list[type[Any]]:
    """Import ``packages`` recursively and register every marked class found.

    ``exclude`` holds fnmatch globs matched against full dotted module names.
    Re-exported classes (the same class object visible in several modules)
    are deduplicated; a name collision between *different* classes raises the
    registry's duplicate-name ``PyleafError``. Module import failures abort
    the scan with a ``PyleafError`` naming the module and the original error.
    """
    registered: list[type[Any]] = []
    seen: set[int] = {id(defn.bean_type) for defn in ctx._registry.all()}
    for package in packages:
        if _is_excluded(package, exclude):
            continue
        pkg = _import_module(package)
        registered.extend(_register_module_classes(ctx, pkg, seen))
        for module_name in _walk_module_names(pkg, exclude):
            module = _import_module(module_name)
            registered.extend(_register_module_classes(ctx, module, seen))
    return registered
