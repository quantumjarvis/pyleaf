"""Transaction errors for pyleaf-tx (subclass the core PyleafError)."""

from pyleaf.core.errors import PyleafError


class TransactionError(PyleafError):
    """Base class for all pyleaf transaction errors."""


class NoTransactionError(TransactionError):
    """Raised when an operation requires an active transaction.

    The transaction analogue of Spring's mandatory-propagation violation:
    a ``Propagation.MANDATORY`` boundary reached with no active transaction.
    """


class IllegalTransactionStateError(TransactionError):
    """Raised when a transaction operation is invalid in the current state.

    Covers: ``init()`` while a transaction context is already active,
    binding/unbinding/registering without an active context, and
    ``Propagation.NEVER`` violations.
    """


class UnexpectedRollbackError(TransactionError):
    """Raised when a commit is attempted on a rollback-only transaction."""
