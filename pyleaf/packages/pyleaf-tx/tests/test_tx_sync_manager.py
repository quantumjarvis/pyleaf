"""Pin TransactionSynchronizationManager semantics.

Contract under test (design-3.1):

* active/inactive lifecycle: ``init`` / ``clear`` / ``is_active``.
* mutators require an active context and raise IllegalTransactionStateError
  when inactive; readers are lenient (None / False / empty tuple).
* ``unbind_resource`` returns the resource and raises KeyError on a miss.
* synchronizations are returned in registration order.
* ``suspend`` fully deactivates and returns a holder; ``resume`` installs it;
  nested suspend/resume pairs restore correctly.
* contextvars copy-on-task-create: a task spawned AFTER init sees the tx;
  a task spawned BEFORE init never sees bindings the parent makes later;
  a task sees the state as of its spawn time (copy-on-write snapshots).
* 50 concurrent asyncio tasks show no cross-talk; raw threads and explicit
  ``Context.run`` give isolation; threads sharing ONE Context object share
  bindings (documented caveat).
"""

import asyncio
import contextvars
import threading
from typing import Any

import pytest
from pyleaf.tx import (
    CompletionStatus,
    IllegalTransactionStateError,
    Isolation,
)
from pyleaf.tx import (
    TransactionSynchronizationManager as TSM,
)


@pytest.fixture(autouse=True)
def clean_context() -> Any:
    TSM.clear()
    yield
    TSM.clear()


# --- lifecycle ------------------------------------------------------------


def test_inactive_by_default() -> None:
    assert not TSM.is_active()
    assert TSM.get_resource("k") is None
    assert not TSM.has_resource("k")
    assert TSM.get_synchronizations() == ()
    assert TSM.current_transaction_name() is None
    assert not TSM.is_current_transaction_read_only()
    assert TSM.current_transaction_isolation_level() is None


def test_init_makes_active_and_clear_resets() -> None:
    TSM.init()
    assert TSM.is_active()
    TSM.clear()
    assert not TSM.is_active()


def test_init_twice_raises() -> None:
    TSM.init()
    with pytest.raises(IllegalTransactionStateError):
        TSM.init()


def test_clear_when_inactive_is_noop() -> None:
    TSM.clear()
    TSM.clear()
    assert not TSM.is_active()


# --- resources -------------------------------------------------------------


def test_bind_get_has_unbind_roundtrip() -> None:
    TSM.init()
    resource = object()
    TSM.bind_resource("ds", resource)
    assert TSM.get_resource("ds") is resource
    assert TSM.has_resource("ds")
    assert TSM.unbind_resource("ds") is resource
    assert TSM.get_resource("ds") is None
    assert not TSM.has_resource("ds")


def test_bind_replaces_existing_key() -> None:
    TSM.init()
    TSM.bind_resource("ds", "first")
    TSM.bind_resource("ds", "second")
    assert TSM.get_resource("ds") == "second"


def test_bind_when_inactive_raises() -> None:
    with pytest.raises(IllegalTransactionStateError):
        TSM.bind_resource("ds", object())


def test_unbind_when_inactive_raises() -> None:
    with pytest.raises(IllegalTransactionStateError):
        TSM.unbind_resource("ds")


def test_unbind_missing_key_raises_keyerror() -> None:
    TSM.init()
    with pytest.raises(KeyError):
        TSM.unbind_resource("missing")


# --- synchronizations -------------------------------------------------------


def test_register_synchronization_when_inactive_raises() -> None:
    with pytest.raises(IllegalTransactionStateError):
        TSM.register_synchronization(object())


def test_synchronizations_are_ordered() -> None:
    TSM.init()
    calls: list[tuple[int, CompletionStatus]] = []

    class PartialSync:
        """Only implements after_completion — hooks are optional."""

        def __init__(self, tag: int) -> None:
            self.tag = tag

        def after_completion(self, status: CompletionStatus) -> None:
            calls.append((self.tag, status))

    s1, s2 = PartialSync(1), PartialSync(2)
    TSM.register_synchronization(s1)
    TSM.register_synchronization(s2)
    assert TSM.get_synchronizations() == (s1, s2)
    for sync in TSM.get_synchronizations():
        sync.after_completion(CompletionStatus.COMMITTED)
    assert calls == [(1, CompletionStatus.COMMITTED), (2, CompletionStatus.COMMITTED)]


# --- transaction metadata ----------------------------------------------------


def test_transaction_metadata_roundtrip() -> None:
    TSM.init()
    TSM.set_current_transaction_name("tx.orders")
    TSM.set_current_transaction_read_only(True)
    TSM.set_current_transaction_isolation_level(Isolation.READ_COMMITTED)
    assert TSM.current_transaction_name() == "tx.orders"
    assert TSM.is_current_transaction_read_only()
    assert TSM.current_transaction_isolation_level() is Isolation.READ_COMMITTED


def test_metadata_setters_when_inactive_raise() -> None:
    with pytest.raises(IllegalTransactionStateError):
        TSM.set_current_transaction_name("x")
    with pytest.raises(IllegalTransactionStateError):
        TSM.set_current_transaction_read_only(True)
    with pytest.raises(IllegalTransactionStateError):
        TSM.set_current_transaction_isolation_level(Isolation.DEFAULT)


# --- suspend / resume ---------------------------------------------------------


def test_suspend_when_inactive_returns_none() -> None:
    assert TSM.suspend() is None


def test_resume_none_is_noop() -> None:
    TSM.resume(None)
    assert not TSM.is_active()


def test_suspend_clears_and_resume_restores() -> None:
    TSM.init()
    TSM.bind_resource("ds", "conn")
    TSM.register_synchronization(object())
    TSM.set_current_transaction_name("tx1")
    TSM.set_current_transaction_read_only(True)
    TSM.set_current_transaction_isolation_level(Isolation.SERIALIZABLE)

    holder = TSM.suspend()
    assert holder is not None
    # Pinned: suspend fully deactivates (Spring-faithful).
    assert not TSM.is_active()
    assert TSM.get_resource("ds") is None
    assert TSM.get_synchronizations() == ()

    TSM.resume(holder)
    assert TSM.is_active()
    assert TSM.get_resource("ds") == "conn"
    assert len(TSM.get_synchronizations()) == 1
    assert TSM.current_transaction_name() == "tx1"
    assert TSM.is_current_transaction_read_only()
    assert TSM.current_transaction_isolation_level() is Isolation.SERIALIZABLE


def test_nested_suspend_stack() -> None:
    """suspend -> init+bind -> suspend -> resume(inner) -> resume(outer)."""
    TSM.init()
    TSM.bind_resource("outer", "o")
    outer = TSM.suspend()

    TSM.init()
    TSM.bind_resource("inner", "i")
    inner = TSM.suspend()
    assert not TSM.is_active()

    TSM.resume(inner)
    assert TSM.get_resource("inner") == "i"
    assert TSM.get_resource("outer") is None

    TSM.resume(outer)
    assert TSM.get_resource("outer") == "o"
    assert TSM.get_resource("inner") is None


def test_suspend_while_suspended_returns_none() -> None:
    TSM.init()
    first = TSM.suspend()
    assert first is not None
    # After a suspend the context is inactive, so a second suspend is None.
    assert TSM.suspend() is None
    TSM.resume(first)
    assert TSM.is_active()


def test_resume_replaces_current_state() -> None:
    """resume() installs the holder's snapshot, discarding current bindings."""
    TSM.init()
    TSM.bind_resource("a", 1)
    holder = TSM.suspend()
    TSM.init()
    TSM.bind_resource("b", 2)
    TSM.resume(holder)
    assert TSM.get_resource("a") == 1
    assert TSM.get_resource("b") is None


# --- asyncio isolation --------------------------------------------------------


async def test_fifty_tasks_isolated() -> None:
    """50 concurrent tasks each init/bind/unbind — no cross-talk."""

    async def worker(i: int) -> tuple[object, bool]:
        TSM.init()
        key = f"res-{i}"
        marker = object()
        TSM.bind_resource(key, marker)
        TSM.register_synchronization(object())
        await asyncio.sleep(0)  # force interleaving
        seen = TSM.get_resource(key)
        leaked = any(
            TSM.has_resource(f"res-{j}") for j in range(50) if j != i
        )
        TSM.clear()
        return seen, leaked

    results = await asyncio.gather(*(worker(i) for i in range(50)))
    for seen, leaked in results:
        assert seen is not None
        assert not leaked


async def test_task_spawned_after_init_sees_transaction() -> None:
    TSM.init()
    TSM.bind_resource("ds", "conn")

    async def child() -> tuple[bool, object]:
        return TSM.is_active(), TSM.get_resource("ds")

    active, res = await asyncio.create_task(child())
    assert active
    assert res == "conn"


async def test_task_spawned_before_init_does_not_see_bindings() -> None:
    """contextvars copies the context at task creation — a task spawned
    BEFORE the parent's init never sees bindings made afterwards."""
    child_started = asyncio.Event()
    parent_bound = asyncio.Event()
    result: list[tuple[bool, object]] = []

    async def child() -> None:
        child_started.set()
        await parent_bound.wait()
        result.append((TSM.is_active(), TSM.get_resource("ds")))

    task = asyncio.create_task(child())  # context snapshotted here
    await child_started.wait()
    TSM.init()
    TSM.bind_resource("ds", "conn")
    parent_bound.set()
    await task
    assert result == [(False, None)]


async def test_task_sees_state_as_of_spawn_time() -> None:
    """Copy-on-write snapshots: a task spawned after init sees the state at
    spawn — not the parent's later mutations."""
    TSM.init()
    TSM.bind_resource("a", 1)

    async def child() -> tuple[bool, object, bool]:
        return TSM.is_active(), TSM.get_resource("a"), TSM.has_resource("b")

    task = asyncio.create_task(child())
    TSM.bind_resource("b", 2)
    TSM.unbind_resource("a")
    active, a, has_b = await task
    assert active
    assert a == 1
    assert not has_b


# --- thread / context isolation ------------------------------------------------


def test_raw_threads_have_isolated_contexts() -> None:
    TSM.init()
    TSM.bind_resource("ds", "main")
    seen: list[tuple[bool, object]] = []

    def worker() -> None:
        seen.append((TSM.is_active(), TSM.get_resource("ds")))
        TSM.init()
        TSM.bind_resource("ds", "worker")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen == [(False, None)]
    assert TSM.get_resource("ds") == "main"  # worker bindings did not leak


def test_explicit_context_run_gives_isolation() -> None:
    TSM.init()
    TSM.bind_resource("ds", "main")
    other = contextvars.Context()

    def probe() -> tuple[bool, object]:
        was_inactive = not TSM.is_active()
        TSM.init()
        TSM.bind_resource("ds", "copy")
        return was_inactive, TSM.get_resource("ds")

    assert other.run(probe) == (True, "copy")
    assert TSM.get_resource("ds") == "main"  # main context untouched


def test_threads_sharing_one_context_share_bindings() -> None:
    """Documented caveat: threads running inside the SAME Context object see
    the same bindings — bind per-context, not per-thread."""
    shared = contextvars.Context()
    shared.run(TSM.init)
    shared.run(TSM.bind_resource, "ds", "shared-conn")
    seen: list[object] = []

    def worker() -> None:
        seen.append(shared.run(TSM.get_resource, "ds"))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen == ["shared-conn"]
