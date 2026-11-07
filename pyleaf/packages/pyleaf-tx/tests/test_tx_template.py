"""TransactionTemplate: programmatic boundaries equivalent to @Transactional."""

from __future__ import annotations

from typing import Any

import pytest
from pyleaf.tx import (
    NoTransactionError,
    Propagation,
    TransactionDefinition,
    TransactionError,
    TransactionTemplate,
    UnexpectedRollbackError,
)
from pyleaf.tx import (
    TransactionSynchronizationManager as TSM,
)
from tx_fakes import AsyncOnlyManager, RecordingManager, SyncOnlyManager


@pytest.fixture(autouse=True)
def clean_context() -> Any:
    TSM.clear()
    yield
    TSM.clear()


def _raise(exc: BaseException) -> Any:
    def run() -> Any:
        raise exc

    return run


def test_execute_sync_matches_decorator_semantics() -> None:
    mgr = RecordingManager()
    template = TransactionTemplate(mgr)

    assert template.execute(lambda: "ok") == "ok"
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]
    assert not TSM.is_active()

    mgr.reset()
    with pytest.raises(ValueError, match="boom"):
        template.execute(_raise(ValueError("boom")))
    assert mgr.calls == [("begin", "REQUIRED"), ("rollback", "tx1")]


async def test_execute_async_matches_decorator_semantics() -> None:
    mgr = RecordingManager()
    template = TransactionTemplate(mgr)

    async def work() -> str:
        assert TSM.is_active()
        return "ok"

    assert await template.execute(work) == "ok"
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]
    assert not TSM.is_active()


def test_with_block_commits_and_rolls_back() -> None:
    mgr = RecordingManager()
    template = TransactionTemplate(mgr)

    with template.required():
        assert TSM.is_active()
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]

    mgr.reset()
    with pytest.raises(ValueError, match="boom"):
        with template.required():
            raise ValueError("boom")
    assert mgr.calls == [("begin", "REQUIRED"), ("rollback", "tx1")]
    assert not TSM.is_active()


async def test_async_with_block_commits_and_rolls_back() -> None:
    mgr = RecordingManager()
    template = TransactionTemplate(mgr)

    async with template.required():
        assert TSM.is_active()
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]

    mgr.reset()
    with pytest.raises(ValueError, match="boom"):
        async with template.required():
            raise ValueError("boom")
    assert mgr.calls == [("begin", "REQUIRED"), ("rollback", "tx1")]


def test_with_definition_overrides_the_boundary() -> None:
    mgr = RecordingManager()
    template = TransactionTemplate(mgr).with_definition(
        TransactionDefinition(propagation=Propagation.MANDATORY)
    )
    with pytest.raises(NoTransactionError):
        template.execute(lambda: "x")
    assert mgr.calls == []


def test_execute_sync_rejects_async_only_manager() -> None:
    template = TransactionTemplate(AsyncOnlyManager())
    with pytest.raises(TransactionError, match="TransactionManager"):
        template.execute(lambda: "x")


async def test_execute_async_rejects_sync_only_manager() -> None:
    template = TransactionTemplate(SyncOnlyManager())

    async def work() -> str:
        return "x"

    with pytest.raises(TransactionError, match="AsyncTransactionManager"):
        await template.execute(work)
    # The half-begun boundary must not leave the context active.
    assert not TSM.is_active()


def test_with_rejects_async_only_manager() -> None:
    template = TransactionTemplate(AsyncOnlyManager())
    with pytest.raises(TransactionError, match="TransactionManager"):
        with template.required():
            pass


async def test_async_with_rejects_sync_only_manager() -> None:
    template = TransactionTemplate(SyncOnlyManager())
    with pytest.raises(TransactionError, match="AsyncTransactionManager"):
        async with template.required():
            pass
    assert not TSM.is_active()


def test_execute_rejects_generators() -> None:
    mgr = RecordingManager()
    template = TransactionTemplate(mgr)

    def gen() -> Any:
        yield 1

    async def agen() -> Any:
        yield 1

    with pytest.raises(TypeError, match="generator"):
        template.execute(gen)
    with pytest.raises(TypeError, match="generator"):
        template.execute(agen)
    assert mgr.calls == []


def test_joined_template_marks_rollback_only() -> None:
    """A template boundary inside an active tx joins it; a rolls-back-on
    failure marks the shared status without driving completion."""
    mgr = RecordingManager()
    outer = TransactionTemplate(mgr)
    inner = TransactionTemplate(mgr)

    def nested() -> str:
        with pytest.raises(ValueError, match="boom"):
            inner.execute(_raise(ValueError("boom")))
        return "done"

    # The join marked the shared status rollback-only: the outer commit
    # rolls back and raises UnexpectedRollbackError.
    with pytest.raises(UnexpectedRollbackError):
        outer.execute(nested)
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRED"),
        ("join", "tx1"),
        ("commit", "tx1"),
        ("unexpected_rollback", "tx1"),
    ]
