"""Transaction infrastructure for pyleaf (WI 3.1, PYL-32).

Data structures (TransactionDefinition/Status), the manager and
synchronization protocols, and the contextvars-bound
TransactionSynchronizationManager. Interceptors (3.2) and concrete
resource managers (3.4) build on this layer.
"""

from .definition import Isolation, Propagation, TransactionDefinition
from .errors import (
    IllegalTransactionStateError,
    NoTransactionError,
    TransactionError,
    UnexpectedRollbackError,
)
from .manager import (
    AsyncTransactionManager,
    CompletionStatus,
    TransactionManager,
    TransactionSynchronization,
)
from .status import TransactionStatus
from .sync import SuspendedResources, TransactionSynchronizationManager

__all__ = [
    "AsyncTransactionManager",
    "CompletionStatus",
    "IllegalTransactionStateError",
    "Isolation",
    "NoTransactionError",
    "Propagation",
    "SuspendedResources",
    "TransactionDefinition",
    "TransactionError",
    "TransactionManager",
    "TransactionStatus",
    "TransactionSynchronization",
    "TransactionSynchronizationManager",
    "UnexpectedRollbackError",
]
