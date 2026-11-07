"""Transaction definition: propagation, isolation and the rollback rule."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class Propagation(Enum):
    """How a transactional boundary behaves relative to a current tx."""

    REQUIRED = auto()
    REQUIRES_NEW = auto()
    NESTED = auto()
    SUPPORTS = auto()
    NOT_SUPPORTED = auto()
    MANDATORY = auto()
    NEVER = auto()


class Isolation(Enum):
    """Isolation level requested for the transaction."""

    DEFAULT = auto()
    READ_UNCOMMITTED = auto()
    READ_COMMITTED = auto()
    REPEATABLE_READ = auto()
    SERIALIZABLE = auto()


@dataclass(frozen=True)
class TransactionDefinition:
    """Immutable description of a requested transaction.

    Rollback convention (pinned by tests — deliberately broader than
    Spring's default, which rolls back only on RuntimeException/Error):
    pyleaf rolls back for ANY ``Exception`` subclass unless the exception
    is an instance of a type in ``no_rollback_for``. ``BaseException``-only
    failures (KeyboardInterrupt, SystemExit, GeneratorExit) never roll
    back unless explicitly listed in ``rollback_for``.

    ``rolls_back_on`` pins the exact rule::

        isinstance(exc, rollback_for) and not isinstance(exc, no_rollback_for)
    """

    propagation: Propagation = Propagation.REQUIRED
    isolation: Isolation = Isolation.DEFAULT
    read_only: bool = False
    timeout_s: float | None = None
    rollback_for: tuple[type[BaseException], ...] = (Exception,)
    no_rollback_for: tuple[type[BaseException], ...] = ()

    def rolls_back_on(self, exc: BaseException) -> bool:
        """Return True if a transaction with this definition rolls back for ``exc``."""
        return isinstance(exc, self.rollback_for) and not isinstance(
            exc, self.no_rollback_for
        )
