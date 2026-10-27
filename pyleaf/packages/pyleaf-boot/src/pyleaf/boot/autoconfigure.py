"""``@AutoConfiguration``: entry-point discovered, ordered configuration.

An auto-configuration is a ``@Configuration`` class (``@Bean`` methods and
``@ConditionalOn*`` marks work identically) discovered through the
``pyleaf.autoconfigure`` entry-point group instead of package scanning.
Ordering decorators — ``@AutoConfigureAfter`` / ``@AutoConfigureBefore`` /
``@AutoConfigureOrder`` — control registration order; a topological sort
with a deterministic tiebreak resolves it.

Loading never raises: a broken entry point is captured by
:func:`discover_autoconfigs` and surfaces as an unmatched
condition-report entry via :func:`register_autoconfigs`.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeVar, overload

from pyleaf.core import PyleafError
from pyleaf.core.stereotypes import CONFIGURATION_ATTR, ConfigurationMeta

if TYPE_CHECKING:
    from pyleaf.core.context import ApplicationContext

T = TypeVar("T")

AUTOCONFIGURE_ATTR = "__pyleaf_autoconfigure__"
AFTER_ATTR = "__pyleaf_autoconfigure_after__"
BEFORE_ATTR = "__pyleaf_autoconfigure_before__"
ORDER_VALUE_ATTR = "__pyleaf_autoconfigure_order__"

ENTRY_POINT_GROUP = "pyleaf.autoconfigure"
EXCLUDE_PROPERTY = "pyleaf.autoconfigure.exclude"


@overload
def AutoConfiguration(target: type[T], /) -> type[T]: ...


@overload
def AutoConfiguration(
    target: str | None = None,
    /,
    *,
    name: str | None = None,
) -> Callable[[type[T]], type[T]]: ...


def AutoConfiguration(
    target: type[T] | str | None = None,
    /,
    *,
    name: str | None = None,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Mark a class as an entry-point-discovered ``@Configuration``.

    Sets both the ``__pyleaf_configuration__`` metadata — so the class goes
    through the normal ``ctx.register`` dispatch — and the
    ``__pyleaf_autoconfigure__`` marker used by ordering/discovery.
    """
    effective = name if name is not None else (target if isinstance(target, str) else None)

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, CONFIGURATION_ATTR, ConfigurationMeta(effective))
        setattr(cls, AUTOCONFIGURE_ATTR, True)
        return cls

    if target is None or isinstance(target, str):
        return wrap
    return wrap(target)


def is_autoconfiguration(value: Any) -> bool:
    """True when ``value`` is a class marked ``@AutoConfiguration``."""
    return isinstance(value, type) and bool(getattr(value, AUTOCONFIGURE_ATTR, False))


def AutoConfigureAfter(
    *refs: type[Any] | str,
) -> Callable[[type[T]], type[T]]:
    """This auto-config must be registered after the referenced classes.

    Refs are auto-config classes or ``"module.Class"`` import strings.
    """

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, AFTER_ATTR, tuple(refs))
        return cls

    return wrap


def AutoConfigureBefore(
    *refs: type[Any] | str,
) -> Callable[[type[T]], type[T]]:
    """This auto-config must be registered before the referenced classes."""

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, BEFORE_ATTR, tuple(refs))
        return cls

    return wrap


def AutoConfigureOrder(value: int) -> Callable[[type[T]], type[T]]:
    """Numeric tiebreak for auto-config ordering (lower first, default 0)."""

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, ORDER_VALUE_ATTR, int(value))
        return cls

    return wrap


def _class_key(cls: type[Any]) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _order_key(cls: type[Any]) -> tuple[int, str]:
    order = getattr(cls, ORDER_VALUE_ATTR, 0)
    if not isinstance(order, int):
        order = 0
    return (order, _class_key(cls))


def sort_autoconfigs(classes: Sequence[type[Any]]) -> list[type[Any]]:
    """Topologically sort auto-config classes by after/before edges.

    Edges that reference classes outside ``classes`` are ignored. Ties are
    broken by ``@AutoConfigureOrder`` then the qualified class name, so the
    result is deterministic. An unresolvable cycle raises ``PyleafError``
    naming the remaining members.
    """
    nodes = list(dict.fromkeys(classes))
    prereqs: dict[type[Any], set[type[Any]]] = {cls: set() for cls in nodes}
    by_key: dict[str, type[Any]] = {}
    for cls in nodes:
        by_key.setdefault(_class_key(cls), cls)
        by_key.setdefault(cls.__qualname__, cls)
        by_key.setdefault(cls.__name__, cls)

    def resolve(ref: type[Any] | str) -> type[Any] | None:
        if isinstance(ref, type):
            return ref if ref in prereqs else None
        return by_key.get(ref)

    for cls in nodes:
        for ref in getattr(cls, AFTER_ATTR, ()):
            target = resolve(ref)
            if target is not None:
                prereqs[cls].add(target)
        for ref in getattr(cls, BEFORE_ATTR, ()):
            target = resolve(ref)
            if target is not None:
                prereqs[target].add(cls)

    result: list[type[Any]] = []
    ready = [cls for cls in nodes if not prereqs[cls]]
    while ready:
        ready.sort(key=_order_key)
        cls = ready.pop(0)
        result.append(cls)
        for other, deps in prereqs.items():
            if cls in deps:
                deps.discard(cls)
                if not deps:
                    ready.append(other)

    if len(result) != len(nodes):
        leftover = sorted((c for c in nodes if c not in result), key=_order_key)
        names = ", ".join(_class_key(cls) for cls in leftover)
        raise PyleafError(
            "Circular @AutoConfigureAfter/@AutoConfigureBefore dependencies"
            f" among auto-configurations: {names}"
        )
    return result


def discover_autoconfigs() -> dict[str, type[Any] | Exception]:
    """Load every ``pyleaf.autoconfigure`` entry point.

    Returns ``{entry-point name: loaded class}``; ``ep.load()`` failures
    are captured into the value slot as the exception, never raised.
    """
    discovered: dict[str, type[Any] | Exception] = {}
    for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        try:
            discovered[ep.name] = ep.load()
        except Exception as exc:
            discovered[ep.name] = exc
    return discovered


def _excluded_names(ctx: ApplicationContext, exclude: Iterable[str]) -> set[str]:
    """Merge the explicit exclude list with the environment property."""
    names = set(exclude)
    raw = ctx.environment.get(EXCLUDE_PROPERTY)
    if raw:
        names.update(part.strip() for part in str(raw).split(",") if part.strip())
    return names


def _matches_exclusion(ep_name: str, cls: type[Any], excluded: set[str]) -> bool:
    return (
        ep_name in excluded
        or _class_key(cls) in excluded
        or cls.__qualname__ in excluded
        or cls.__name__ in excluded
    )


def register_autoconfigs(
    ctx: ApplicationContext,
    discovered: Mapping[str, type[Any] | Exception],
    exclude: Iterable[str] = (),
) -> list[type[Any]]:
    """Sort and register the discovered auto-config classes on ``ctx``.

    Exclusion applies by entry-point name or ``module.Class`` (plus the
    ``pyleaf.autoconfigure.exclude`` environment property). Failed loads
    are skipped but recorded as unmatched condition-report entries, so
    ``ctx.condition_report`` (and ``pyleaf conditions``) shows them.

    Call this after user scanning/registration so a user bean already in
    the registry satisfies ``@ConditionalOnMissingBean`` — and so the
    report entries recorded here precede the refresh-time entries.
    Returns the registered classes in applied order.
    """
    excluded = _excluded_names(ctx, exclude)
    classes: list[type[Any]] = []
    for ep_name, loaded in discovered.items():
        if isinstance(loaded, type):
            if _matches_exclusion(ep_name, loaded, excluded):
                continue
            classes.append(loaded)
        else:
            ctx.record_condition(
                ep_name, False, [f"entry point load failed: {loaded}"]
            )
    ordered = sort_autoconfigs(classes)
    for cls in ordered:
        ctx.register(cls)
    return ordered
