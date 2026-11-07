"""Rollback rules + completion ordering through the interceptor.

Covers: the default rollback rule, ``no_rollback_for`` commits,
``rollback_for`` narrowing, the swallowed-inner → rollback-only →
``UnexpectedRollbackError`` path, ``BaseException`` propagating
untouched, and synchronization ordering (before_commit → commit →
after_commit → after_completion on success; after_completion(ROLLED_BACK)
alone on rollback).
"""

from __future__ import annotations

from typing import Any

import pytest
from pyleaf.core import ApplicationContext, Component
from pyleaf.tx import (
    Propagation,
    Transactional,
    TxAutoConfiguration,
    UnexpectedRollbackError,
)
from pyleaf.tx import (
    TransactionSynchronizationManager as TSM,
)
from tx_fakes import RecordingManager, RecordingManagerBean, RecordingSync


@pytest.fixture(autouse=True)
def clean_context() -> Any:
    TSM.clear()
    yield
    TSM.clear()


def _ctx(*classes: type) -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


class Retryable(Exception):
    pass


class Fatal(Exception):
    pass


@Component
class RollbackService:
    def __init__(self, manager: RecordingManagerBean) -> None:
        self.mgr: RecordingManager = manager
        self.log: list[tuple[str, Any]] = []

    @Transactional
    def fails_default(self) -> None:
        raise ValueError("boom")

    @Transactional(no_rollback_for=(ValueError,))
    def fails_no_rollback(self) -> None:
        raise ValueError("boom")

    @Transactional(rollback_for=(Fatal,))
    def fails_unlisted(self) -> None:
        raise ValueError("not listed")

    @Transactional(rollback_for=(Fatal,))
    def fails_listed(self) -> None:
        raise Fatal("listed")

    @Transactional
    def fails_base(self) -> None:
        raise KeyboardInterrupt("ctrl-c")

    @Transactional
    def registers_sync(self) -> None:
        TSM.register_synchronization(RecordingSync(self.log, "s1"))

    @Transactional
    def registers_sync_then_fails(self) -> None:
        TSM.register_synchronization(RecordingSync(self.log, "s1"))
        raise ValueError("boom")

    @Transactional
    def inner_fails(self) -> None:
        raise ValueError("inner boom")

    @Transactional
    def outer_swallows(self) -> str:
        """Join the inner tx, swallow its failure, commit the outer."""
        try:
            self.inner_fails()
        except ValueError:
            pass
        return "swallowed"

    @Transactional
    def outer_propagates(self) -> None:
        self.inner_fails()


def _svc() -> tuple[RollbackService, RecordingManager]:
    ctx = _ctx(RecordingManagerBean, RollbackService, TxAutoConfiguration)
    svc = ctx.get(RollbackService)
    return svc, svc.mgr


def test_default_rolls_back_on_exception() -> None:
    svc, mgr = _svc()
    with pytest.raises(ValueError, match="boom"):
        svc.fails_default()
    assert mgr.calls == [("begin", "REQUIRED"), ("rollback", "tx1")]
    assert not TSM.is_active()


def test_no_rollback_for_commits_then_propagates() -> None:
    svc, mgr = _svc()
    with pytest.raises(ValueError, match="boom"):
        svc.fails_no_rollback()
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]


def test_rollback_for_narrows_the_rule() -> None:
    svc, mgr = _svc()
    # ValueError is not a Fatal → commit path, exception still propagates.
    with pytest.raises(ValueError, match="not listed"):
        svc.fails_unlisted()
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]
    # A Fatal matches → rollback.
    mgr.reset()
    with pytest.raises(Fatal, match="listed"):
        svc.fails_listed()
    assert mgr.calls == [("begin", "REQUIRED"), ("rollback", "tx1")]


def test_base_exception_propagates_untouched() -> None:
    """KeyboardInterrupt is not in the default rollback_for — the tx
    commits (per the pinned rule) and the BaseException re-raises."""
    svc, mgr = _svc()
    with pytest.raises(KeyboardInterrupt):
        svc.fails_base()
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]
    assert not TSM.is_active()


def test_swallowed_inner_marks_rollback_only() -> None:
    """Inner REQUIRED join throws → shared status rollback-only → the
    outer commit drives a rollback and raises UnexpectedRollbackError."""
    svc, mgr = _svc()
    with pytest.raises(UnexpectedRollbackError):
        svc.outer_swallows()
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRED"),
        ("join", "tx1"),
        ("commit", "tx1"),
        ("unexpected_rollback", "tx1"),
    ]
    assert not TSM.is_active()


def test_propagating_inner_rolls_back_outer() -> None:
    svc, mgr = _svc()
    with pytest.raises(ValueError, match="inner boom"):
        svc.outer_propagates()
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRED"),
        ("join", "tx1"),
        ("rollback", "tx1"),
    ]


def test_synchronization_commit_order() -> None:
    svc, _mgr = _svc()
    svc.registers_sync()
    assert svc.log == [
        ("s1", "before_commit"),
        ("s1", "after_commit"),
        ("s1", "after_completion", "COMMITTED"),
    ]


def test_synchronization_rollback_order() -> None:
    svc, _mgr = _svc()
    with pytest.raises(ValueError, match="boom"):
        svc.registers_sync_then_fails()
    # before_commit/after_commit are NOT invoked on the rollback path.
    assert svc.log == [("s1", "after_completion", "ROLLED_BACK")]


# --- nested + suspend interplay ----------------------------------------------


@Component
class MixedService:
    def __init__(self, manager: RecordingManagerBean) -> None:
        self.mgr: RecordingManager = manager

    @Transactional(propagation=Propagation.NESTED)
    def nested_fails(self) -> None:
        raise ValueError("nested boom")

    @Transactional
    def outer_swallows_nested(self) -> str:
        try:
            self.nested_fails()
        except ValueError:
            pass
        return "ok"

    @Transactional(propagation=Propagation.REQUIRES_NEW)
    def inner_new_fails(self) -> None:
        raise ValueError("new boom")

    @Transactional
    def outer_swallows_new(self) -> str:
        try:
            self.inner_new_fails()
        except ValueError:
            pass
        return "ok"


def test_nested_rollback_to_savepoint_outer_still_commits() -> None:
    """A rolled-back NESTED inner does NOT mark the outer rollback-only —
    the outer commits after swallowing the savepoint rollback."""
    ctx = _ctx(RecordingManagerBean, MixedService, TxAutoConfiguration)
    svc = ctx.get(MixedService)
    assert svc.outer_swallows_nested() == "ok"
    assert svc.mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "NESTED"),
        ("nested", "savepoint2"),
        ("rollback", "savepoint2"),
        ("commit", "tx1"),
    ]


def test_requires_new_rolls_back_inner_only() -> None:
    """REQUIRES_NEW failure rolls back only the fresh tx; a swallowing
    outer still commits."""
    ctx = _ctx(RecordingManagerBean, MixedService, TxAutoConfiguration)
    svc = ctx.get(MixedService)
    assert svc.outer_swallows_new() == "ok"
    assert svc.mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRES_NEW"),
        ("rollback", "tx2"),
        ("commit", "tx1"),
    ]
