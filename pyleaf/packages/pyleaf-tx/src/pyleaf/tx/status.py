"""Transaction status: the mutable per-transaction handle."""

from dataclasses import dataclass
from typing import Any


@dataclass
class TransactionStatus:
    """Handle for one in-flight transaction, returned by ``begin``.

    ``transaction`` is the opaque manager-owned underlying object (a
    connection, session, savepoint …); callers must not interpret it.
    ``is_new`` distinguishes a freshly begun transaction from one joined
    via REQUIRED/SUPPORTS propagation; ``has_savepoint`` marks NESTED
    transactions. ``rollback_only`` and ``completed`` start False; the
    owning TransactionManager sets ``completed`` on commit/rollback.
    """

    transaction: Any = None
    is_new: bool = True
    has_savepoint: bool = False
    rollback_only: bool = False
    completed: bool = False

    def set_rollback_only(self) -> None:
        """Mark this transaction rollback-only; a later commit must fail."""
        self.rollback_only = True

    def is_rollback_only(self) -> bool:
        """Return True once :meth:`set_rollback_only` has been called."""
        return self.rollback_only

    def set_completed(self) -> None:
        """Mark the transaction finished (committed or rolled back)."""
        self.completed = True

    def is_completed(self) -> bool:
        """Return True once commit or rollback has run."""
        return self.completed
