"""Recording fakes for the tx tests.

``RecordingManager`` is dual-capable: its ``begin``/``commit``/``rollback``
are synchronous and return awaitable results, so it satisfies BOTH the
``TransactionManager`` path (return values used directly) and the
``AsyncTransactionManager`` path (``await mgr.begin(...)``). Resource
binding goes through TSM keyed by the manager instance; the bound object
IS the outer ``TransactionStatus`` so a joined boundary marks
rollback-only on the same object the outer commit later inspects.

``SyncOnlyManager`` (dual=False) returns plain non-awaitable results —
the async path must reject it. ``AsyncOnlyManager`` has coroutine
methods — the sync path must reject it. Both keep recording.

Recorded events: ``("begin", prop)``, ``("join", tx)``,
``("nested", sp)``, ``("commit", tx)``, ``("rollback", tx)`` and
``("unexpected_rollback", tx)`` (commit on a rollback-only status rolls
back then raises ``UnexpectedRollbackError`` — Spring's semantics).
"""

from __future__ import annotations

from typing import Any

from pyleaf.core import Component
from pyleaf.tx import (
    Propagation,
    TransactionDefinition,
    TransactionStatus,
    UnexpectedRollbackError,
)
from pyleaf.tx import (
    TransactionSynchronizationManager as TSM,
)


class RecordingStatus(TransactionStatus):
    """A ``TransactionStatus`` that is also awaitable (returns itself)."""

    def __await__(self) -> Any:
        yield from ()
        return self


class _AwaitableResult:
    """Awaitable ``None`` — the dual shape for void manager methods."""

    __slots__ = ("value",)

    def __init__(self, value: Any = None) -> None:
        self.value = value

    def __await__(self) -> Any:
        yield from ()
        return self.value


class RecordingManager:
    """Records manager calls; suspends/resumes via TSM resource binding."""

    def __init__(self, dual: bool = True) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.counter = 0
        self._dual = dual

    def reset(self) -> None:
        """Clear recorded calls and the tx counter."""
        self.calls.clear()
        self.counter = 0

    def _status_cls(self) -> type[TransactionStatus]:
        return RecordingStatus if self._dual else TransactionStatus

    def _new_tx(self, savepoint: bool = False) -> str:
        self.counter += 1
        return f"{'savepoint' if savepoint else 'tx'}{self.counter}"

    def _done(self) -> Any:
        return _AwaitableResult() if self._dual else None

    def _begin_logic(self, definition: TransactionDefinition) -> TransactionStatus:
        existing = TSM.get_resource(self)
        self.calls.append(("begin", definition.propagation.name))
        if existing is not None:
            if definition.propagation is Propagation.NESTED:
                status = self._status_cls()(
                    transaction=self._new_tx(savepoint=True),
                    is_new=False,
                    has_savepoint=True,
                )
                self.calls.append(("nested", status.transaction))
                return status
            self.calls.append(("join", existing.transaction))
            return existing
        status = self._status_cls()(transaction=self._new_tx(), is_new=True)
        TSM.bind_resource(self, status)
        return status

    def _commit_logic(self, status: TransactionStatus) -> Any:
        self.calls.append(("commit", status.transaction))
        if status.is_rollback_only():
            return self._rollback_logic(status, unexpected=True)
        status.set_completed()
        if status.is_new:
            TSM.unbind_resource(self)
        return self._done()

    def _rollback_logic(
        self, status: TransactionStatus, *, unexpected: bool = False
    ) -> Any:
        event = "unexpected_rollback" if unexpected else "rollback"
        self.calls.append((event, status.transaction))
        status.set_completed()
        if status.is_new:
            TSM.unbind_resource(self)
        if unexpected:
            raise UnexpectedRollbackError(
                f"{status.transaction} was marked rollback-only"
            )
        return self._done()

    # Dual shape: sync-declared, awaitable results.

    def begin(self, definition: TransactionDefinition) -> TransactionStatus:
        return self._begin_logic(definition)

    def commit(self, status: TransactionStatus) -> Any:
        return self._commit_logic(status)

    def rollback(self, status: TransactionStatus) -> Any:
        return self._rollback_logic(status)


@Component
class RecordingManagerBean(RecordingManager):
    """The dual manager registered as a bean (by-type resolution)."""

    def __init__(self) -> None:
        super().__init__()


@Component
class SyncOnlyManager(RecordingManager):
    """Sync-only shape: ``begin``/``commit``/``rollback`` return plain
    results — the async boundary must reject it."""

    def __init__(self) -> None:
        super().__init__(dual=False)


@Component
class AsyncOnlyManager(RecordingManager):
    """Async-only shape: coroutine methods — the sync boundary must
    reject it. Recording logic is shared with ``RecordingManager``."""

    def __init__(self) -> None:
        super().__init__()

    async def begin(self, definition: TransactionDefinition) -> TransactionStatus:
        return self._begin_logic(definition)

    async def commit(self, status: TransactionStatus) -> Any:
        return self._commit_logic(status)

    async def rollback(self, status: TransactionStatus) -> Any:
        return self._rollback_logic(status)


class RecordingSync:
    """A synchronization recording hook invocation order."""

    def __init__(self, log: list[tuple[str, Any]], tag: str = "sync") -> None:
        self.log = log
        self.tag = tag

    def before_commit(self) -> None:
        self.log.append((self.tag, "before_commit"))

    def after_commit(self) -> None:
        self.log.append((self.tag, "after_commit"))

    def after_completion(self, status: Any) -> None:
        self.log.append((self.tag, "after_completion", status.name))
