"""Environment: named property sources, type coercion and active profiles."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .errors import PyleafError

_TRUE_WORDS = {"true", "1", "yes", "on"}
_FALSE_WORDS = {"false", "0", "no", "off"}


def _to_bool(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
        raise PyleafError(f"Cannot convert {value!r} to bool")
    return bool(value)


def _coerce(value: Any, type_: Callable[[Any], Any]) -> Any:
    if type_ is bool:
        return _to_bool(value)
    return type_(value)


class PropertySource:
    """A named, flat source of property values."""

    def __init__(self, name: str, values: Mapping[str, Any]) -> None:
        self.name = name
        self.values: dict[str, Any] = dict(values)

    def get(self, key: str) -> Any:
        """Return the raw value for ``key`` or None when absent."""
        return self.values.get(key)

    def __contains__(self, key: object) -> bool:
        return key in self.values

    def __repr__(self) -> str:
        return f"PropertySource({self.name!r}, {len(self.values)} values)"


class EnvVarSource(PropertySource):
    """Property source backed by ``os.environ``.

    Only variables starting with ``prefix`` are exposed; the prefix is
    stripped, the rest is lowercased and ``_`` becomes ``.`` — so
    ``PYLEAF_FOO_BAR`` maps to the property key ``foo.bar``.
    """

    def __init__(self, prefix: str = "PYLEAF_") -> None:
        values = {
            var[len(prefix) :].lower().replace("_", "."): value
            for var, value in os.environ.items()
            if var.startswith(prefix)
        }
        super().__init__(name=f"env:{prefix}", values=values)


class Environment:
    """Ordered property sources plus the set of active profiles.

    Later sources in ``sources`` override earlier ones for the same key
    (list order = lowest to highest precedence).
    """

    def __init__(
        self,
        sources: Sequence[PropertySource] = (),
        active_profiles: Sequence[str] = (),
    ) -> None:
        self._sources = list(sources)
        profiles = frozenset(active_profiles)
        self._active_profiles = profiles or frozenset({"default"})

    @property
    def active_profiles(self) -> frozenset[str]:
        """Active profiles; ``{"default"}`` when none were configured."""
        return self._active_profiles

    @property
    def sources(self) -> list[PropertySource]:
        """The ordered property sources (lowest to highest precedence)."""
        return list(self._sources)

    def get(
        self,
        key: str,
        type_: Callable[[Any], Any] | None = None,
        default: Any = None,
    ) -> Any:
        """Return the value for ``key``, coerced to ``type_`` when given.

        With no ``type_`` the raw value is returned. Missing keys return
        ``default``; bool coercion accepts "true"/"1"/"yes"/"on" (and the
        matching false words).
        """
        for source in reversed(self._sources):
            if key in source:
                value = source.get(key)
                if type_ is None:
                    return value
                return _coerce(value, type_)
        return default

    def require(self, key: str) -> Any:
        """Return the raw value for ``key``; raise PyleafError when absent."""
        sentinel = object()
        value = self.get(key, default=sentinel)
        if value is sentinel:
            raise PyleafError(f"Missing required property: '{key}'")
        return value
