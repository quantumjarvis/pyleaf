"""Bean definitions and the bean registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from .errors import NoSuchBean, PyleafError

if TYPE_CHECKING:
    from collections.abc import Callable

    from .resolver import DepSpec

DEFAULT_ORDER = 2**31 - 1


class Scope(Enum):
    """Bean scope."""

    SINGLETON = auto()
    PROTOTYPE = auto()
    REQUEST = auto()


def _no_dep_specs() -> list[DepSpec]:
    return []


@dataclass
class BeanDefinition:
    """Static description of a bean: name, type, factory and metadata."""

    name: str
    bean_type: Any
    factory: Callable[[], Any]
    scope: Scope = Scope.SINGLETON
    primary: bool = False
    lazy: bool = True
    order: int = DEFAULT_ORDER
    dep_specs: Callable[[], list[DepSpec]] = _no_dep_specs
    # Conditions marked by @ConditionalOn* decorators (class or @Bean method).
    conditions: list[Any] = field(default_factory=list)
    # Internal beans are framework plumbing registered by the context itself
    # (environment, eventPublisher, applicationContext): injectable, but
    # never advised or scanned for event listeners.
    internal: bool = False


class BeanRegistry:
    """Name-keyed registry of bean definitions."""

    def __init__(self) -> None:
        self._defs: dict[str, BeanDefinition] = {}
        self._candidates: dict[Any, tuple[BeanDefinition, ...]] = {}

    def register(self, defn: BeanDefinition) -> None:
        """Register a definition; duplicate names are an error."""
        if defn.name in self._defs:
            raise PyleafError(f"Duplicate bean name: '{defn.name}'")
        self._defs[defn.name] = defn
        # A new definition can join the candidate set of any already-resolved
        # type, so every memoized candidate list is now stale.
        self._candidates.clear()

    def has(self, name: str) -> bool:
        """Return True when a bean with ``name`` is registered."""
        return name in self._defs

    def remove(self, name: str) -> BeanDefinition:
        """Drop the definition for ``name``; raise NoSuchBean when absent."""
        try:
            defn = self._defs.pop(name)
        except KeyError:
            raise NoSuchBean(f"No bean named '{name}'") from None
        # The removed definition may be in memoized candidate lists.
        self._candidates.clear()
        return defn

    def get(self, name: str) -> BeanDefinition:
        """Return the definition for ``name`` or raise NoSuchBean."""
        try:
            return self._defs[name]
        except KeyError:
            raise NoSuchBean(f"No bean named '{name}'") from None

    def candidates(self, required: Any) -> list[BeanDefinition]:
        """Return all definitions whose bean_type is a subclass of ``required``.

        Results are memoized per ``required`` type and invalidated by
        :meth:`register`. A fresh list is returned each call so callers may
        filter or sort it without corrupting the cache.
        """
        try:
            hit = self._candidates.get(required)
        except TypeError:
            # ``required`` is unhashable; resolve without caching.
            hit = None
        if hit is None:
            found: list[BeanDefinition] = []
            for defn in self._defs.values():
                try:
                    if not issubclass(defn.bean_type, required):
                        continue
                except TypeError:
                    # bean_type or required is not a class
                    # (e.g. non-checkable Protocol)
                    continue
                if defn.internal and defn.bean_type is not required:
                    # Internal plumbing resolves by its exact declared type
                    # only, so e.g. applicationContext (which implements
                    # EventPublisher) does not shadow the eventPublisher bean.
                    continue
                found.append(defn)
            hit = tuple(found)
            try:
                self._candidates[required] = hit
            except TypeError:
                pass
        return list(hit)

    def all(self) -> list[BeanDefinition]:
        """Return all registered definitions in registration order."""
        return list(self._defs.values())
