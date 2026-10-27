"""A logging @Aspect that counts intercepted invocations per method."""

import logging
from typing import Any

from pyleaf.core import Around, Aspect, Invocation, within

_logger = logging.getLogger(__name__)


@Aspect
class InvocationCounterAspect:
    """Count every call to a ``hello.*`` bean method, keyed by method name."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    @Around(within("hello."))
    def count_invocations(self, inv: Invocation) -> Any:
        """Increment the per-method counter, log, then proceed."""
        self.counts[inv.method_name] = self.counts.get(inv.method_name, 0) + 1
        _logger.info(
            "invocation #%d of %s.%s",
            self.counts[inv.method_name],
            type(inv.target).__name__,
            inv.method_name,
        )
        return inv.proceed()
