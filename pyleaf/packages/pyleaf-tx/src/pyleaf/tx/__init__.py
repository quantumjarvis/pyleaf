"""Transaction infrastructure for pyleaf (WI 3.1-3.3, PYL-32/33/34).

Data structures (TransactionDefinition/Status), the manager and
synchronization protocols, the contextvars-bound
TransactionSynchronizationManager, the ``@Transactional`` marker and its
AOP interceptor, the programmatic TransactionTemplate, and
``@TransactionalEventListener`` phase-bound event delivery. Concrete
resource managers (3.4) build on this layer.
"""

from .autoconfigure import TxAutoConfiguration
from .definition import Isolation, Propagation, TransactionDefinition
from .errors import (
    IllegalTransactionStateError,
    NoTransactionError,
    TransactionError,
    UnexpectedRollbackError,
)
from .events import (
    TX_EVENT_ATTR,
    TransactionalEventListener,
    TxEventListenerPostProcessor,
    TxEventSynchronization,
    TxPhase,
)
from .interceptor import TransactionInterceptor
from .manager import (
    AsyncTransactionManager,
    CompletionStatus,
    TransactionManager,
    TransactionSynchronization,
)
from .status import TransactionStatus
from .sync import SuspendedResources, TransactionSynchronizationManager
from .template import TransactionTemplate
from .transactional import (
    TRANSACTIONAL_MARKER,
    TX_META_ATTR,
    Transactional,
    TransactionalMeta,
    transactional_meta,
)

__all__ = [
    "TRANSACTIONAL_MARKER",
    "TX_EVENT_ATTR",
    "TX_META_ATTR",
    "AsyncTransactionManager",
    "CompletionStatus",
    "IllegalTransactionStateError",
    "Isolation",
    "NoTransactionError",
    "Propagation",
    "SuspendedResources",
    "TransactionDefinition",
    "TransactionError",
    "TransactionInterceptor",
    "TransactionManager",
    "TransactionStatus",
    "TransactionSynchronization",
    "TransactionSynchronizationManager",
    "TransactionTemplate",
    "Transactional",
    "TransactionalEventListener",
    "TransactionalMeta",
    "TxAutoConfiguration",
    "TxEventListenerPostProcessor",
    "TxEventSynchronization",
    "TxPhase",
    "UnexpectedRollbackError",
    "transactional_meta",
]
