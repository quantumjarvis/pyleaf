"""The ``HealthIndicator`` protocol aggregated by ``/actuator/health``.

Indicators are discovered structurally: any bean whose class provides a
``health()`` method is a candidate (the runtime-checkable Protocol match
in ``BeanRegistry.candidates``), so inheriting from ``HealthIndicator``
is optional.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HealthIndicator(Protocol):
    """Contribute one component to the aggregated health response.

    ``health()`` must return a mapping containing ``"status"`` —
    ``"UP"`` or ``"DOWN"``. Any component reporting ``"DOWN"`` flips the
    overall status (and the HTTP code) to DOWN/503; extra keys land in
    the component's detail section.
    """

    def health(self) -> Mapping[str, Any]:
        """Return this component's health mapping (must include ``status``)."""
        ...


class PingHealthIndicator:
    """The built-in always-UP indicator (bean ``pingHealthIndicator``)."""

    def health(self) -> Mapping[str, Any]:
        return {"status": "UP"}


__all__ = ["HealthIndicator", "PingHealthIndicator"]
