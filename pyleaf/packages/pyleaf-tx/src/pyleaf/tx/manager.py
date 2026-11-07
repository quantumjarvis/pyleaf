"""Transaction manager and synchronization protocols."""

from __future__ import annotations

from enum import Enum, auto
from typing import Protocol, runtime_checkable

from .definition import TransactionDefinition
from .status import TransactionStatus


class CompletionStatus(Enum):
    """How a transaction finished; passed to ``after_completion``."""

    COMMITTED = auto()
    ROLLED_BACK = auto()


@runtime_checkable
class TransactionManager(Protocol):
    """Synchronous transaction manager (Spring's PlatformTransactionManager)."""

    def begin(self, definition: TransactionDefinition) -> TransactionStatus:
        """Start (or join, per ``definition.propagation``) a transaction."""
        ...

    def commit(self, status: TransactionStatus) -> None:
        """Commit ``status``; raises UnexpectedRollbackError if rollback-only."""
        ...

    def rollback(self, status: TransactionStatus) -> None:
        """Roll back ``status``."""
        ...


@runtime_checkable
class AsyncTransactionManager(Protocol):
    """Async transaction manager — same contract, awaitable methods."""

    async def begin(self, definition: TransactionDefinition) -> TransactionStatus:
        """Start (or join, per ``definition.propagation``) a transaction."""
        ...

    async def commit(self, status: TransactionStatus) -> None:
        """Commit ``status``; raises UnexpectedRollbackError if rollback-only."""
        ...

    async def rollback(self, status: TransactionStatus) -> None:
        """Roll back ``status``."""
        ...


class TransactionSynchronization(Protocol):
    """Optional commit/rollback hooks invoked by transaction managers.

    All hooks are optional: a synchronization may implement any subset.
    Managers must therefore dispatch via ``getattr`` — this protocol is
    deliberately NOT ``runtime_checkable``, because an isinstance check
    would reject partial implementations. Hooks run in registration order
    (see TransactionSynchronizationManager.get_synchronizations).
    """

    def before_commit(self) -> None:
        """Called before the commit is issued."""
        ...

    def after_commit(self) -> None:
        """Called after a successful commit."""
        ...

    def after_completion(self, status: CompletionStatus) -> None:
        """Called after commit or rollback, with the outcome."""
        ...
