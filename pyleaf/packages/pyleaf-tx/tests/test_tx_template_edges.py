"""Mutation-hardening tests for ``pyleaf.tx.template`` (PYL-42).

``mutmut run`` on template.py produced 129 survivors; this suite pins the
behaviors they probed — the ``_plan`` decision table field-by-field,
violation message text (owner name travels into errors), manager-shape
rejection at every entry point, TSM transaction metadata visible inside a
boundary, partial ``TransactionSynchronization`` objects, async commit /
rollback / savepoint ordering, and the joined/savepoint rollback rules
under ``no_rollback_for``.

The remaining ~9 survivors are equivalent mutants — no test can kill
them because they don't change observable behavior:

* ``SyncBoundary.__init__``/``AsyncBoundary.__init__`` mutmut_6
  (``self._inited = None``): ``_inited`` is only ever tested for truth
  and is unconditionally assigned before any such test.
* ``_cleanup`` mutmut_1/2/5 on both boundaries (``_inited = None`` /
  ``True``, ``_suspended = ""``): ``_cleanup`` runs exactly once per
  boundary, after the last read of those fields — the stored value is
  never observed again.
* ``_TemplateTransaction.__init__`` mutmut_3 (``_boundary = ""``): the
  field is unconditionally overwritten by ``__enter__``/``__aenter__``
  before ``__exit__``/``__aexit__`` reads it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pyleaf.tx import (
    IllegalTransactionStateError,
    Isolation,
    NoTransactionError,
    Propagation,
    TransactionDefinition,
    TransactionError,
    TransactionTemplate,
    UnexpectedRollbackError,
)
from pyleaf.tx.sync import TransactionSynchronizationManager as TSM
from pyleaf.tx.template import _Plan, _plan
from tx_fakes import (
    AsyncOnlyManager,
    RecordingManager,
    RecordingSync,
    SyncOnlyManager,
)


@pytest.fixture(autouse=True)
def clean_context() -> Any:
    TSM.clear()
    yield
    TSM.clear()


def _defn(**kwargs: Any) -> TransactionDefinition:
    return TransactionDefinition(**kwargs)


# --- _plan: the propagation decision table ------------------------------------


@pytest.mark.parametrize(
    ("propagation", "active", "expected"),
    [
        (Propagation.NEVER, False, _Plan(False, False, False)),
        (Propagation.MANDATORY, True, _Plan(False, False, True)),
        (Propagation.NOT_SUPPORTED, True, _Plan(True, False, False)),
        (Propagation.NOT_SUPPORTED, False, _Plan(False, False, False)),
        (Propagation.SUPPORTS, False, _Plan(False, False, False)),
        (Propagation.SUPPORTS, True, _Plan(False, False, True)),
        (Propagation.REQUIRED, False, _Plan(False, True, True)),
        (Propagation.REQUIRED, True, _Plan(False, False, True)),
        (Propagation.REQUIRES_NEW, False, _Plan(False, True, True)),
        (Propagation.REQUIRES_NEW, True, _Plan(True, True, True)),
        (Propagation.NESTED, False, _Plan(False, True, True)),
        (Propagation.NESTED, True, _Plan(False, False, True)),
    ],
)
def test_plan_decision_table(
    propagation: Propagation, active: bool, expected: _Plan
) -> None:
    """Every field of the plan is pinned — a flipped suspend/init/begin
    flag must change the plan's value, not just its truthiness."""
    assert _plan(propagation, active, "owner") == expected


# --- violation messages carry the boundary name --------------------------------


def test_never_violation_message_names_the_boundary() -> None:
    mgr = RecordingManager()
    outer = TransactionTemplate(mgr)
    inner = TransactionTemplate(
        mgr, _defn(propagation=Propagation.NEVER)
    )
    with outer.required():
        with pytest.raises(IllegalTransactionStateError) as excinfo:
            with inner.required():
                pass
    assert str(excinfo.value) == (
        "TransactionTemplate.required: Propagation.NEVER violation —"
        " an existing transaction was found"
    )


def test_mandatory_violation_message_names_the_boundary() -> None:
    mgr = RecordingManager()
    tmpl = TransactionTemplate(mgr, _defn(propagation=Propagation.MANDATORY))
    with pytest.raises(NoTransactionError) as excinfo:
        with tmpl.required():
            pass
    assert str(excinfo.value) == (
        "TransactionTemplate.required: Propagation.MANDATORY violation —"
        " no existing transaction found"
    )


async def test_async_never_violation_message_names_the_boundary() -> None:
    mgr = RecordingManager()
    outer = TransactionTemplate(mgr)
    inner = TransactionTemplate(
        mgr, _defn(propagation=Propagation.NEVER)
    )
    async with outer.required():
        with pytest.raises(IllegalTransactionStateError) as excinfo:
            async with inner.required():
                pass
    assert str(excinfo.value) == (
        "TransactionTemplate.required: Propagation.NEVER violation —"
        " an existing transaction was found"
    )


# --- TSM metadata visible inside the boundary ----------------------------------


def test_boundary_publishes_tx_metadata() -> None:
    mgr = RecordingManager()
    tmpl = TransactionTemplate(
        mgr,
        _defn(read_only=True, isolation=Isolation.SERIALIZABLE),
    )
    with tmpl.required():
        assert (
            TSM.current_transaction_name() == "TransactionTemplate.required"
        )
        assert TSM.is_current_transaction_read_only() is True
        assert (
            TSM.current_transaction_isolation_level()
            is Isolation.SERIALIZABLE
        )
    assert not TSM.is_active()


def test_default_boundary_does_not_publish_isolation() -> None:
    mgr = RecordingManager()
    with TransactionTemplate(mgr).required():
        assert TSM.is_current_transaction_read_only() is False
        assert TSM.current_transaction_isolation_level() is None


async def test_async_boundary_publishes_tx_metadata() -> None:
    mgr = RecordingManager()
    tmpl = TransactionTemplate(
        mgr,
        _defn(read_only=True, isolation=Isolation.SERIALIZABLE),
    )
    async with tmpl.required():
        assert (
            TSM.current_transaction_name() == "TransactionTemplate.required"
        )
        assert TSM.is_current_transaction_read_only() is True
        assert (
            TSM.current_transaction_isolation_level()
            is Isolation.SERIALIZABLE
        )


# --- manager-shape rejection at every entry point -------------------------------


class _CoroutineBeginManager(RecordingManager):
    """A sync *function* ``begin`` returning a coroutine — slips past the
    ``iscoroutinefunction`` pre-check into ``_SyncBoundary._begin``."""

    def begin(self, definition: TransactionDefinition) -> Any:
        return self._begin_coro(definition)

    async def _begin_coro(
        self, definition: TransactionDefinition
    ) -> Any:
        return self._begin_logic(definition)


def test_sync_boundary_rejects_coroutine_begin_result() -> None:
    mgr = _CoroutineBeginManager()
    with pytest.raises(TransactionError) as excinfo:
        with TransactionTemplate(mgr).required():
            pass
    assert (
        "async-only — a synchronous method requires a TransactionManager"
        in str(excinfo.value)
    )
    assert not TSM.is_active()


def test_with_sync_rejects_manager_without_begin() -> None:
    with pytest.raises(TransactionError) as excinfo:
        with TransactionTemplate(object()).required():
            pass
    assert "TransactionManager" in str(excinfo.value)


def test_with_sync_rejects_async_manager_exact_message() -> None:
    with pytest.raises(TransactionError) as excinfo:
        with TransactionTemplate(AsyncOnlyManager()).required():
            pass
    assert str(excinfo.value) == (
        "TransactionTemplate.required(): a synchronous 'with' block"
        " requires a TransactionManager (sync begin/commit/rollback)"
    )


async def test_async_with_rejects_manager_without_begin() -> None:
    with pytest.raises(TransactionError) as excinfo:
        async with TransactionTemplate(object()).required():
            pass
    assert str(excinfo.value) == (
        "TransactionTemplate.required(): an 'async with' block"
        " requires an AsyncTransactionManager"
    )


async def test_async_boundary_rejects_nonawaitable_begin_message() -> None:
    with pytest.raises(TransactionError) as excinfo:
        async with TransactionTemplate(SyncOnlyManager()).required():
            pass
    assert (
        "is synchronous — an async method requires an"
        " AsyncTransactionManager" in str(excinfo.value)
    )


def test_execute_rejects_generators_exact_message() -> None:
    def gen() -> Any:
        yield

    with pytest.raises(TypeError) as excinfo:
        TransactionTemplate(RecordingManager()).execute(gen)
    assert str(excinfo.value) == (
        "TransactionTemplate.execute: generator and"
        " async-generator functions cannot be transactional"
    )


def test_execute_sync_rejects_manager_without_begin() -> None:
    with pytest.raises(TransactionError) as excinfo:
        TransactionTemplate(object()).execute(lambda: None)
    assert "requires a TransactionManager" in str(excinfo.value)


def test_execute_sync_rejects_async_manager_message() -> None:
    with pytest.raises(TransactionError) as excinfo:
        TransactionTemplate(AsyncOnlyManager()).execute(lambda: None)
    assert str(excinfo.value).endswith(
        "a synchronous function requires a TransactionManager"
        " (sync begin/commit/rollback)"
    )


async def test_execute_async_rejects_manager_without_begin() -> None:
    async def work() -> None:
        return None

    with pytest.raises(TransactionError) as excinfo:
        await TransactionTemplate(object()).execute(work)
    assert str(excinfo.value).endswith(
        "an async function requires an AsyncTransactionManager"
    )


# --- boundary naming ------------------------------------------------------------


def test_execute_uses_function_qualname_as_tx_name() -> None:
    mgr = RecordingManager()

    def work() -> str:
        return str(TSM.current_transaction_name())

    name = TransactionTemplate(mgr).execute(work)
    assert name == (
        "test_execute_uses_function_qualname_as_tx_name.<locals>.work"
    )


def test_execute_falls_back_to_default_name_for_unnamed_callable() -> None:
    class CallableObj:
        def __call__(self) -> str:
            return str(TSM.current_transaction_name())

    mgr = RecordingManager()
    assert (
        TransactionTemplate(mgr).execute(CallableObj())
        == "TransactionTemplate.execute"
    )


async def test_execute_async_uses_function_qualname_as_tx_name() -> None:
    mgr = RecordingManager()

    async def work() -> str:
        return str(TSM.current_transaction_name())

    name = await TransactionTemplate(mgr).execute(work)
    assert name == (
        "test_execute_async_uses_function_qualname_as_tx_name"
        ".<locals>.work"
    )


# --- partial TransactionSynchronization ------------------------------------------


class _PartialSync:
    """Only ``after_completion`` — the other hooks are absent."""

    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log

    def after_completion(self, status: Any) -> None:
        self.log.append(("completion", status.name))


class _NoCompletionSync:
    """Only ``before_commit``/``after_commit`` — ``after_completion``
    is absent."""

    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log

    def before_commit(self) -> None:
        self.log.append(("before",))

    def after_commit(self) -> None:
        self.log.append(("after",))


def test_synchronization_with_missing_hooks_is_tolerated() -> None:
    """The getattr defaults matter: a sync missing before_commit /
    after_commit must not crash the boundary."""
    mgr = RecordingManager()
    log: list[tuple[str, Any]] = []

    def body() -> None:
        TSM.register_synchronization(_PartialSync(log))

    TransactionTemplate(mgr).execute(body)
    assert log == [("completion", "COMMITTED")]


def test_synchronization_without_after_completion_is_tolerated() -> None:
    """A sync missing ``after_completion`` must not crash either —
    its present hooks still fire."""
    mgr = RecordingManager()
    log: list[tuple[str, Any]] = []

    def body() -> None:
        TSM.register_synchronization(_NoCompletionSync(log))

    TransactionTemplate(mgr).execute(body)
    assert log == [("before",), ("after",)]


# --- async synchronization ordering ---------------------------------------------


async def test_async_synchronization_commit_order() -> None:
    mgr = RecordingManager()
    log: list[tuple[str, Any]] = []

    async def body() -> None:
        TSM.register_synchronization(RecordingSync(log, "s1"))

    await TransactionTemplate(mgr).execute(body)
    assert log == [
        ("s1", "before_commit"),
        ("s1", "after_commit"),
        ("s1", "after_completion", "COMMITTED"),
    ]


async def test_async_synchronization_rollback_order() -> None:
    mgr = RecordingManager()
    log: list[tuple[str, Any]] = []

    async def body() -> None:
        TSM.register_synchronization(RecordingSync(log, "s1"))
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await TransactionTemplate(mgr).execute(body)
    # No before_commit/after_commit on the rollback path.
    assert log == [("s1", "after_completion", "ROLLED_BACK")]


class _FailingCommitManager(RecordingManager):
    """commit() raises — _commit_outer must notify ROLLED_BACK."""

    def commit(self, status: Any) -> Any:
        self.calls.append(("commit", status.transaction))
        raise RuntimeError("commit failed")


async def test_async_commit_failure_notifies_rolled_back() -> None:
    mgr = _FailingCommitManager()
    log: list[tuple[str, Any]] = []

    async def body() -> None:
        TSM.register_synchronization(RecordingSync(log, "s1"))

    with pytest.raises(RuntimeError, match="commit failed"):
        await TransactionTemplate(mgr).execute(body)
    assert ("s1", "before_commit") in log
    assert log[-1] == ("s1", "after_completion", "ROLLED_BACK")


# --- async-only manager used from the async path --------------------------------


async def test_async_execute_with_async_only_manager_records_commit() -> None:
    """``_maybe_await`` must really await commit/rollback coroutines —
    an un-awaited coroutine never records its event."""
    mgr = AsyncOnlyManager()

    async def body() -> str:
        return "done"

    assert await TransactionTemplate(mgr).execute(body) == "done"
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]
    assert not TSM.is_active()


# --- exit(): no_rollback_for on the outer (inited) boundary ----------------------


async def test_async_no_rollback_for_commits_then_propagates() -> None:
    mgr = RecordingManager()
    tmpl = TransactionTemplate(mgr, _defn(no_rollback_for=(ValueError,)))

    async def body() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await tmpl.execute(body)
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]


# --- exit(): the savepoint branch ------------------------------------------------


def test_nested_savepoint_commits_when_exc_not_rollback() -> None:
    """NESTED + no_rollback_for: the savepoint is RELEASED (committed)
    while the exception still propagates to the outer rollback."""
    mgr = RecordingManager()
    outer = TransactionTemplate(mgr)
    inner = TransactionTemplate(
        mgr,
        _defn(
            propagation=Propagation.NESTED, no_rollback_for=(ValueError,)
        ),
    )

    def outer_body() -> None:
        def inner_body() -> None:
            raise ValueError("nested")

        inner.execute(inner_body)

    with pytest.raises(ValueError, match="nested"):
        outer.execute(outer_body)
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "NESTED"),
        ("nested", "savepoint2"),
        ("commit", "savepoint2"),  # released — exc matched no_rollback_for
        ("rollback", "tx1"),  # outer rolls back on the propagating exc
    ]


async def test_async_nested_savepoint_rolls_back_matching_exc() -> None:
    mgr = RecordingManager()
    outer = TransactionTemplate(mgr)
    inner = TransactionTemplate(
        mgr, _defn(propagation=Propagation.NESTED)
    )

    async def outer_body() -> str:
        async def inner_body() -> None:
            raise ValueError("nested")

        with pytest.raises(ValueError, match="nested"):
            await inner.execute(inner_body)
        return "ok"

    assert await outer.execute(outer_body) == "ok"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "NESTED"),
        ("nested", "savepoint2"),
        ("rollback", "savepoint2"),
        ("commit", "tx1"),
    ]


async def test_async_nested_savepoint_commits_when_exc_not_rollback() -> None:
    mgr = RecordingManager()
    outer = TransactionTemplate(mgr)
    inner = TransactionTemplate(
        mgr,
        _defn(
            propagation=Propagation.NESTED, no_rollback_for=(ValueError,)
        ),
    )

    async def outer_body() -> None:
        async def inner_body() -> None:
            raise ValueError("nested")

        await inner.execute(inner_body)

    with pytest.raises(ValueError, match="nested"):
        await outer.execute(outer_body)
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "NESTED"),
        ("nested", "savepoint2"),
        ("commit", "savepoint2"),
        ("rollback", "tx1"),
    ]


# --- exit(): the joined (non-savepoint) branch ------------------------------------


def test_joined_inner_non_rollback_exception_outer_commits() -> None:
    """A joined boundary whose exc doesn't match rollback rules must NOT
    mark the shared status rollback-only — the outer commits."""
    mgr = RecordingManager()
    outer = TransactionTemplate(mgr)
    inner = TransactionTemplate(mgr, _defn(no_rollback_for=(ValueError,)))

    def outer_body() -> str:
        def inner_body() -> None:
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            inner.execute(inner_body)
        return "ok"

    assert outer.execute(outer_body) == "ok"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRED"),
        ("join", "tx1"),
        ("commit", "tx1"),
    ]


async def test_async_joined_inner_failure_marks_rollback_only() -> None:
    mgr = RecordingManager()
    outer = TransactionTemplate(mgr)
    inner = TransactionTemplate(mgr)

    async def outer_body() -> None:
        async def inner_body() -> None:
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            await inner.execute(inner_body)

    with pytest.raises(UnexpectedRollbackError):
        await outer.execute(outer_body)
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRED"),
        ("join", "tx1"),
        ("commit", "tx1"),
        ("unexpected_rollback", "tx1"),
    ]


async def test_async_joined_inner_non_rollback_exception_outer_commits() -> None:
    mgr = RecordingManager()
    outer = TransactionTemplate(mgr)
    inner = TransactionTemplate(mgr, _defn(no_rollback_for=(ValueError,)))

    async def outer_body() -> str:
        async def inner_body() -> None:
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            await inner.execute(inner_body)
        return "ok"

    assert await outer.execute(outer_body) == "ok"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRED"),
        ("join", "tx1"),
        ("commit", "tx1"),
    ]
