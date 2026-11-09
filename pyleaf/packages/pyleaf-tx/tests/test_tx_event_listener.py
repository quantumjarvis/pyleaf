"""``@TransactionalEventListener``: phase-bound event delivery (WI 3.3, PYL-34).

A listener marked with the tx variant is scanned like a normal
``@EventListener`` (the marker sets ``__pyleaf_eventlistener__``), but the
``TxEventListenerPostProcessor`` bean wraps each marked bound method so a
call inside a transaction defers delivery to a per-transaction
``TxEventSynchronization``; outside a transaction the call is delivered
immediately (``fallback_execution=True``) or dropped with a debug log.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from pyleaf.core import ApplicationContext, Component, EventPublisher
from pyleaf.core.events import EVENT_LISTENER_ATTR
from pyleaf.tx import (
    Transactional,
    TransactionalEventListener,
    TransactionSynchronizationManager,
    TxAutoConfiguration,
    TxEventListenerPostProcessor,
    TxEventSynchronization,
    TxPhase,
)
from pyleaf.tx.events import TX_EVENT_ATTR
from tx_fakes import RecordingManagerBean

TSM = TransactionSynchronizationManager


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


class Placed:
    """A plain event object — no base class or interface required."""

    def __init__(self, tag: str) -> None:
        self.tag = tag


@Component
class Recorder:
    def __init__(self) -> None:
        self.entries: list[Any] = []


# --- marker shape ---------------------------------------------------------------


def test_marker_sets_core_and_tx_attrs() -> None:
    @TransactionalEventListener(phase=TxPhase.BEFORE_COMMIT, fallback_execution=False)
    def on_placed(self: Any, event: Placed) -> None: ...

    assert getattr(on_placed, EVENT_LISTENER_ATTR) is True
    assert getattr(on_placed, TX_EVENT_ATTR) == (TxPhase.BEFORE_COMMIT, False)


def test_marker_defaults() -> None:
    @TransactionalEventListener
    def on_placed(self: Any, event: Placed) -> None: ...

    assert getattr(on_placed, TX_EVENT_ATTR) == (TxPhase.AFTER_COMMIT, True)


def test_coroutine_listener_rejected_at_decoration() -> None:
    with pytest.raises(TypeError, match="coroutine"):

        @TransactionalEventListener
        async def bare(self: Any, event: Placed) -> None: ...

    async def target(self: Any, event: Placed) -> None: ...

    with pytest.raises(TypeError, match="coroutine"):
        TransactionalEventListener(phase=TxPhase.AFTER_COMMIT)(target)


# --- wired context ------------------------------------------------------------


@Component
class AllPhasesListener:
    """One listener per phase; each records whether commit/rollback had
    already been issued by the manager at delivery time."""

    def __init__(self, rec: Recorder, mgr: RecordingManagerBean) -> None:
        self._rec = rec
        self._mgr = mgr

    def _note(self, phase: str, event: Placed) -> None:
        committed = any(call[0] == "commit" for call in self._mgr.calls)
        rolled = any(call[0] == "rollback" for call in self._mgr.calls)
        self._rec.entries.append((phase, event.tag, committed, rolled))

    @TransactionalEventListener(phase=TxPhase.BEFORE_COMMIT)
    def before(self, event: Placed) -> None:
        self._note("before_commit", event)

    @TransactionalEventListener  # default: AFTER_COMMIT
    def after(self, event: Placed) -> None:
        self._note("after_commit", event)

    @TransactionalEventListener(phase=TxPhase.AFTER_ROLLBACK)
    def rolled(self, event: Placed) -> None:
        self._note("after_rollback", event)

    @TransactionalEventListener(phase=TxPhase.AFTER_COMPLETION)
    def done(self, event: Placed) -> None:
        self._note("after_completion", event)


@Component
class OrderService:
    def __init__(self, pub: EventPublisher, rec: Recorder) -> None:
        self._pub = pub
        self._rec = rec

    @Transactional
    def place(self, tag: str = "t") -> str:
        self._pub.publish(Placed(tag))
        # Delivery must NOT have happened during the transactional method.
        self._rec.entries.append(("in_tx", tag))
        return tag

    @Transactional
    def place_twice(self, tag: str = "t") -> None:
        self._pub.publish(Placed(tag))
        self._pub.publish(Placed(tag))
        syncs = [
            s
            for s in TSM.get_synchronizations()
            if isinstance(s, TxEventSynchronization)
        ]
        self._rec.entries.append(("syncs", len(syncs)))

    @Transactional
    def place_then_fail(self, tag: str = "t") -> None:
        self._pub.publish(Placed(tag))
        raise ValueError("order-rejected")


# --- commit path ----------------------------------------------------------------


def test_after_commit_fires_once_after_commit() -> None:
    ctx = _ctx(
        RecordingManagerBean, Recorder, AllPhasesListener, OrderService,
        TxAutoConfiguration,
    )
    svc = ctx.get(OrderService)
    assert svc.place("a") == "a"
    mgr = ctx.get(RecordingManagerBean)
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]
    # Delivery order: in-method publish defers -> before_commit (pre-commit)
    # -> after_commit (post-commit) -> after_completion. AFTER_ROLLBACK
    # never fires on a committed tx.
    assert ctx.get(Recorder).entries == [
        ("in_tx", "a"),
        ("before_commit", "a", False, False),
        ("after_commit", "a", True, False),
        ("after_completion", "a", True, False),
    ]


def test_rollback_fires_rollback_and_completion_not_commit() -> None:
    ctx = _ctx(
        RecordingManagerBean, Recorder, AllPhasesListener, OrderService,
        TxAutoConfiguration,
    )
    svc = ctx.get(OrderService)
    with pytest.raises(ValueError, match="order-rejected"):
        svc.place_then_fail("b")
    mgr = ctx.get(RecordingManagerBean)
    assert mgr.calls == [("begin", "REQUIRED"), ("rollback", "tx1")]
    # AFTER_COMMIT stays silent; AFTER_ROLLBACK then AFTER_COMPLETION fire.
    assert ctx.get(Recorder).entries == [
        ("after_rollback", "b", False, True),
        ("after_completion", "b", False, True),
    ]


def test_one_synchronization_accumulates_all_listeners() -> None:
    ctx = _ctx(
        RecordingManagerBean, Recorder, AllPhasesListener, OrderService,
        TxAutoConfiguration,
    )
    ctx.get(OrderService).place_twice("x")
    rec = ctx.get(Recorder).entries
    # A single TxEventSynchronization carried the events of every listener
    # for both publishes in this transaction.
    assert ("syncs", 1) in rec
    after_commits = [e for e in rec if e[:1] == ("after_commit",)]
    assert len(after_commits) == 2  # one per published event, delivered once


# --- listener failures ------------------------------------------------------------


@Component
class FailingBeforeCommitListener:
    @TransactionalEventListener(phase=TxPhase.BEFORE_COMMIT)
    def on(self, event: Placed) -> None:
        raise RuntimeError("pre-commit-boom")


@Component
class FailingAfterCommitListener:
    @TransactionalEventListener  # AFTER_COMMIT
    def on(self, event: Placed) -> None:
        raise RuntimeError("post-commit-boom")


def test_before_commit_exception_rolls_back() -> None:
    ctx = _ctx(
        RecordingManagerBean, Recorder, AllPhasesListener,
        FailingBeforeCommitListener, OrderService, TxAutoConfiguration,
    )
    svc = ctx.get(OrderService)
    with pytest.raises(RuntimeError, match="pre-commit-boom"):
        svc.place("c")
    mgr = ctx.get(RecordingManagerBean)
    # The commit was never issued (the boundary reports ROLLED_BACK to the
    # synchronizations without driving the manager's commit/rollback).
    assert [c[0] for c in mgr.calls] == ["begin"]
    rec = ctx.get(Recorder).entries
    # AFTER_COMMIT stayed silent; rollback + completion phases delivered.
    assert ("after_commit", "c", False, False) not in rec
    assert ("after_rollback", "c", False, False) in rec
    assert ("after_completion", "c", False, False) in rec
    assert not TSM.is_active()


def test_after_commit_exception_logged_others_delivered_commit_unaffected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ctx = _ctx(
        RecordingManagerBean, Recorder, AllPhasesListener,
        FailingAfterCommitListener, OrderService, TxAutoConfiguration,
    )
    svc = ctx.get(OrderService)
    with caplog.at_level(logging.ERROR, logger="pyleaf.tx.events"):
        assert svc.place("d") == "d"  # does NOT raise
    mgr = ctx.get(RecordingManagerBean)
    assert ("commit", "tx1") in mgr.calls
    rec = ctx.get(Recorder).entries
    # The healthy AFTER_COMMIT listener (same phase, earlier in order) and
    # the AFTER_COMPLETION listener still received the event.
    assert ("after_commit", "d", True, False) in rec
    assert ("after_completion", "d", True, False) in rec
    assert any(
        record.name == "pyleaf.tx.events" and record.exc_info
        for record in caplog.records
    )


# --- outside a transaction ---------------------------------------------------------


@Component
class FallbackListener:
    def __init__(self, rec: Recorder) -> None:
        self._rec = rec

    @TransactionalEventListener(fallback_execution=True)
    def immediate(self, event: Placed) -> None:
        self._rec.entries.append(("fallback", event.tag))

    @TransactionalEventListener(fallback_execution=False)
    def dropped(self, event: Placed) -> None:
        self._rec.entries.append(("dropped", event.tag))


def test_outside_tx_fallback_true_delivers_immediately() -> None:
    ctx = _ctx(Recorder, FallbackListener, TxAutoConfiguration)
    ctx.publish(Placed("e"))
    assert ctx.get(Recorder).entries == [("fallback", "e")]


def test_outside_tx_fallback_false_drops_with_debug_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ctx = _ctx(Recorder, FallbackListener, TxAutoConfiguration)
    with caplog.at_level(logging.DEBUG, logger="pyleaf.tx.events"):
        ctx.publish(Placed("e"))
    # Only the fallback_execution=True listener ran.
    assert ctx.get(Recorder).entries == [("fallback", "e")]
    assert any(
        record.name == "pyleaf.tx.events"
        and record.levelno == logging.DEBUG
        and "Placed" in record.getMessage()
        for record in caplog.records
    )


# --- ordering + wiring -----------------------------------------------------------


@Component
class OrderedListeners:
    def __init__(self, rec: Recorder) -> None:
        self._rec = rec

    @TransactionalEventListener
    def first(self, event: Placed) -> None:
        self._rec.entries.append("first")

    @TransactionalEventListener
    def second(self, event: Placed) -> None:
        self._rec.entries.append("second")


def test_order_within_phase_is_dispatch_order() -> None:
    ctx = _ctx(
        RecordingManagerBean, Recorder, OrderedListeners, OrderService,
        TxAutoConfiguration,
    )
    ctx.get(OrderService).place("f")
    entries = ctx.get(Recorder).entries
    assert entries[entries.index("first") :] == ["first", "second"]


def test_autoconfigure_provides_post_processor_bean() -> None:
    ctx = _ctx(TxAutoConfiguration)
    assert isinstance(
        ctx.get_bean("txEventListenerPostProcessor"),
        TxEventListenerPostProcessor,
    )
