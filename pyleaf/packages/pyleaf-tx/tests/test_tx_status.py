"""Pin TransactionStatus flags, the manager protocols and the error taxonomy."""

import pytest
from pyleaf.core.errors import PyleafError
from pyleaf.tx import (
    AsyncTransactionManager,
    CompletionStatus,
    IllegalTransactionStateError,
    NoTransactionError,
    TransactionDefinition,
    TransactionError,
    TransactionManager,
    TransactionStatus,
    UnexpectedRollbackError,
)


def test_status_defaults() -> None:
    status = TransactionStatus(transaction=object())
    assert status.is_new is True
    assert status.has_savepoint is False
    assert not status.is_rollback_only()
    assert not status.is_completed()


def test_rollback_only_flag() -> None:
    status = TransactionStatus()
    status.set_rollback_only()
    assert status.is_rollback_only()


def test_completed_flag_set_by_manager_on_commit_or_rollback() -> None:
    status = TransactionStatus()
    status.set_completed()
    assert status.is_completed()


def test_nested_savepoint_flag() -> None:
    status = TransactionStatus(is_new=False, has_savepoint=True)
    assert status.is_new is False
    assert status.has_savepoint is True


def test_completion_status_members() -> None:
    assert {s.name for s in CompletionStatus} == {"COMMITTED", "ROLLED_BACK"}


class _SyncManager:
    def begin(self, definition: TransactionDefinition) -> TransactionStatus:
        return TransactionStatus(transaction=self, is_new=True)

    def commit(self, status: TransactionStatus) -> None:
        status.set_completed()

    def rollback(self, status: TransactionStatus) -> None:
        status.set_completed()


class _AsyncManager:
    async def begin(self, definition: TransactionDefinition) -> TransactionStatus:
        return TransactionStatus(transaction=self, is_new=True)

    async def commit(self, status: TransactionStatus) -> None:
        status.set_completed()

    async def rollback(self, status: TransactionStatus) -> None:
        status.set_completed()


def test_sync_manager_protocol_runtime_checkable() -> None:
    assert isinstance(_SyncManager(), TransactionManager)
    assert not isinstance(object(), TransactionManager)


def test_async_manager_protocol_runtime_checkable() -> None:
    # runtime_checkable protocols check method PRESENCE only, not
    # signatures — a sync manager's begin/commit/rollback also satisfy
    # the async protocol structurally. Pinned: same-named methods make
    # the two protocols indistinguishable to isinstance.
    assert isinstance(_AsyncManager(), AsyncTransactionManager)
    assert isinstance(_AsyncManager(), TransactionManager)
    assert isinstance(_SyncManager(), AsyncTransactionManager)
    assert not isinstance(object(), AsyncTransactionManager)


async def test_async_manager_roundtrip() -> None:
    manager = _AsyncManager()
    status = await manager.begin(TransactionDefinition())
    assert status.is_new
    await manager.commit(status)
    assert status.is_completed()


def test_errors_subclass_pyleaf_error() -> None:
    assert issubclass(TransactionError, PyleafError)
    for err in (NoTransactionError, IllegalTransactionStateError, UnexpectedRollbackError):
        assert issubclass(err, TransactionError)
        with pytest.raises(PyleafError):
            raise err("x")
