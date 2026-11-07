"""The seven propagations through the @Transactional interceptor, sync and async.

Every test wires a real ``ApplicationContext``: the service bean, the
``RecordingManagerBean`` and ``TxAutoConfiguration`` are registered and
refreshed, so the assertions exercise the full path — pointcut match,
advice chain, boundary executor, TSM lifecycle and the manager's
recorded call sequence.
"""

from __future__ import annotations

from typing import Any

import pytest
from pyleaf.core import ApplicationContext, Component
from pyleaf.tx import (
    IllegalTransactionStateError,
    NoTransactionError,
    Propagation,
    Transactional,
    TxAutoConfiguration,
)
from pyleaf.tx import (
    TransactionSynchronizationManager as TSM,
)
from tx_fakes import RecordingManager, RecordingManagerBean


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


# --- sync service -----------------------------------------------------------


@Component
class SyncService:
    def __init__(self, manager: RecordingManagerBean) -> None:
        self.mgr: RecordingManager = manager
        self.seen: list[tuple[str, Any]] = []

    @Transactional
    def required(self) -> str:
        self.seen.append(("required", TSM.get_resource(self.mgr)))
        return "ok"

    @Transactional
    def required_outer(self) -> str:
        self.seen.append(("outer", TSM.get_resource(self.mgr)))
        return self.required()

    @Transactional(propagation=Propagation.REQUIRES_NEW)
    def requires_new(self) -> str:
        self.seen.append(("new", TSM.get_resource(self.mgr)))
        return "new"

    @Transactional
    def required_then_new(self) -> str:
        self.seen.append(("outer", TSM.get_resource(self.mgr)))
        return self.requires_new()

    @Transactional(propagation=Propagation.NESTED)
    def nested(self) -> str:
        self.seen.append(("nested", TSM.get_resource(self.mgr)))
        return "nested"

    @Transactional
    def required_then_nested(self) -> str:
        return self.nested()

    @Transactional(propagation=Propagation.SUPPORTS)
    def supports(self) -> str:
        self.seen.append(("supports", TSM.get_resource(self.mgr)))
        return "supports"

    @Transactional
    def required_then_supports(self) -> str:
        return self.supports()

    @Transactional(propagation=Propagation.NOT_SUPPORTED)
    def not_supported(self) -> str:
        self.seen.append(("not-supported-active", TSM.is_active()))
        return "plain"

    @Transactional
    def required_then_not_supported(self) -> str:
        return self.not_supported()

    @Transactional(propagation=Propagation.MANDATORY)
    def mandatory(self) -> str:
        self.seen.append(("mandatory", TSM.get_resource(self.mgr)))
        return "joined"

    @Transactional
    def required_then_mandatory(self) -> str:
        return self.mandatory()

    @Transactional(propagation=Propagation.NEVER)
    def never(self) -> str:
        self.seen.append(("never-active", TSM.is_active()))
        return "never"

    @Transactional
    def required_then_never(self) -> str:
        return self.never()


# --- async service ----------------------------------------------------------


@Component
class AsyncService:
    def __init__(self, manager: RecordingManagerBean) -> None:
        self.mgr: RecordingManager = manager
        self.seen: list[tuple[str, Any]] = []

    @Transactional
    async def required(self) -> str:
        self.seen.append(("required", TSM.get_resource(self.mgr)))
        return "ok"

    @Transactional
    async def required_outer(self) -> str:
        self.seen.append(("outer", TSM.get_resource(self.mgr)))
        return await self.required()

    @Transactional(propagation=Propagation.REQUIRES_NEW)
    async def requires_new(self) -> str:
        self.seen.append(("new", TSM.get_resource(self.mgr)))
        return "new"

    @Transactional
    async def required_then_new(self) -> str:
        self.seen.append(("outer", TSM.get_resource(self.mgr)))
        return await self.requires_new()

    @Transactional(propagation=Propagation.NESTED)
    async def nested(self) -> str:
        self.seen.append(("nested", TSM.get_resource(self.mgr)))
        return "nested"

    @Transactional
    async def required_then_nested(self) -> str:
        return await self.nested()

    @Transactional(propagation=Propagation.SUPPORTS)
    async def supports(self) -> str:
        self.seen.append(("supports", TSM.get_resource(self.mgr)))
        return "supports"

    @Transactional
    async def required_then_supports(self) -> str:
        return await self.supports()

    @Transactional(propagation=Propagation.NOT_SUPPORTED)
    async def not_supported(self) -> str:
        self.seen.append(("not-supported-active", TSM.is_active()))
        return "plain"

    @Transactional
    async def required_then_not_supported(self) -> str:
        return await self.not_supported()

    @Transactional(propagation=Propagation.MANDATORY)
    async def mandatory(self) -> str:
        self.seen.append(("mandatory", TSM.get_resource(self.mgr)))
        return "joined"

    @Transactional
    async def required_then_mandatory(self) -> str:
        return await self.mandatory()

    @Transactional(propagation=Propagation.NEVER)
    async def never(self) -> str:
        self.seen.append(("never-active", TSM.is_active()))
        return "never"

    @Transactional
    async def required_then_never(self) -> str:
        return await self.never()


def _sync() -> tuple[SyncService, RecordingManager]:
    ctx = _ctx(RecordingManagerBean, SyncService, TxAutoConfiguration)
    svc = ctx.get(SyncService)
    return svc, svc.mgr


def _async() -> tuple[AsyncService, RecordingManager]:
    ctx = _ctx(RecordingManagerBean, AsyncService, TxAutoConfiguration)
    svc = ctx.get(AsyncService)
    return svc, svc.mgr


# --- REQUIRED ----------------------------------------------------------------


def test_required_sync() -> None:
    svc, mgr = _sync()
    # No active tx: begins, runs, commits, clears the context.
    assert svc.required() == "ok"
    assert not TSM.is_active()
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]

    # An active tx joins: one physical tx, one commit.
    svc.seen.clear()
    mgr.reset()
    assert svc.required_outer() == "ok"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRED"),
        ("join", "tx1"),
        ("commit", "tx1"),
    ]
    # Both boundaries observed the SAME bound status object.
    outer, inner = svc.seen[0][1], svc.seen[1][1]
    assert outer is inner and outer.is_new


async def test_required_async() -> None:
    svc, mgr = _async()
    assert await svc.required() == "ok"
    assert not TSM.is_active()
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]

    mgr.reset()
    assert await svc.required_outer() == "ok"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRED"),
        ("join", "tx1"),
        ("commit", "tx1"),
    ]


# --- REQUIRES_NEW -------------------------------------------------------------


def test_requires_new_sync() -> None:
    svc, mgr = _sync()
    assert svc.required_then_new() == "new"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRES_NEW"),
        # The inner tx commits BEFORE the outer one resumes + commits.
        ("commit", "tx2"),
        ("commit", "tx1"),
    ]
    outer_status, inner_status = svc.seen[0][1], svc.seen[1][1]
    assert outer_status is not inner_status
    assert inner_status.transaction == "tx2"
    assert not TSM.is_active()


async def test_requires_new_async() -> None:
    svc, mgr = _async()
    assert await svc.required_then_new() == "new"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "REQUIRES_NEW"),
        ("commit", "tx2"),
        ("commit", "tx1"),
    ]
    outer_status, inner_status = svc.seen[0][1], svc.seen[1][1]
    assert outer_status is not inner_status
    assert not TSM.is_active()


# --- NESTED -------------------------------------------------------------------


def test_nested_sync() -> None:
    svc, mgr = _sync()
    assert svc.required_then_nested() == "nested"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "NESTED"),
        ("nested", "savepoint2"),
        # Savepoint released before the outer commit.
        ("commit", "savepoint2"),
        ("commit", "tx1"),
    ]
    # Inside the nested body the bound resource is still the OUTER status.
    assert svc.seen[0][1].transaction == "tx1"
    assert not TSM.is_active()


async def test_nested_async() -> None:
    svc, mgr = _async()
    assert await svc.required_then_nested() == "nested"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "NESTED"),
        ("nested", "savepoint2"),
        ("commit", "savepoint2"),
        ("commit", "tx1"),
    ]
    assert not TSM.is_active()


# --- SUPPORTS -----------------------------------------------------------------


def test_supports_sync() -> None:
    svc, mgr = _sync()
    # No active tx: runs untransacted — no manager interaction at all.
    assert svc.supports() == "supports"
    assert mgr.calls == []
    assert svc.seen[-1] == ("supports", None)

    # Active tx: joins it.
    assert svc.required_then_supports() == "supports"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "SUPPORTS"),
        ("join", "tx1"),
        ("commit", "tx1"),
    ]
    assert svc.seen[-1][1] is not None


async def test_supports_async() -> None:
    svc, mgr = _async()
    assert await svc.supports() == "supports"
    assert mgr.calls == []

    assert await svc.required_then_supports() == "supports"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "SUPPORTS"),
        ("join", "tx1"),
        ("commit", "tx1"),
    ]


# --- NOT_SUPPORTED ------------------------------------------------------------


def test_not_supported_sync() -> None:
    svc, mgr = _sync()
    # Outside a tx: just runs.
    assert svc.not_supported() == "plain"
    assert mgr.calls == []

    # Inside a tx: the context is suspended for the body, then resumed.
    svc.seen.clear()
    assert svc.required_then_not_supported() == "plain"
    assert svc.seen == [("not-supported-active", False)]
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]
    assert not TSM.is_active()


async def test_not_supported_async() -> None:
    svc, mgr = _async()
    assert await svc.not_supported() == "plain"
    assert mgr.calls == []

    svc.seen.clear()
    assert await svc.required_then_not_supported() == "plain"
    assert svc.seen == [("not-supported-active", False)]
    assert mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]


# --- MANDATORY ----------------------------------------------------------------


def test_mandatory_sync() -> None:
    svc, mgr = _sync()
    with pytest.raises(NoTransactionError):
        svc.mandatory()
    assert mgr.calls == []

    assert svc.required_then_mandatory() == "joined"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "MANDATORY"),
        ("join", "tx1"),
        ("commit", "tx1"),
    ]


async def test_mandatory_async() -> None:
    svc, mgr = _async()
    with pytest.raises(NoTransactionError):
        await svc.mandatory()
    assert mgr.calls == []

    assert await svc.required_then_mandatory() == "joined"
    assert mgr.calls == [
        ("begin", "REQUIRED"),
        ("begin", "MANDATORY"),
        ("join", "tx1"),
        ("commit", "tx1"),
    ]


# --- NEVER --------------------------------------------------------------------


def test_never_sync() -> None:
    svc, mgr = _sync()
    # No active tx: runs untransacted.
    assert svc.never() == "never"
    assert svc.seen == [("never-active", False)]
    assert mgr.calls == []

    # An active tx triggers the violation; the outer tx rolls back.
    with pytest.raises(IllegalTransactionStateError):
        svc.required_then_never()
    assert mgr.calls == [("begin", "REQUIRED"), ("rollback", "tx1")]
    assert not TSM.is_active()


async def test_never_async() -> None:
    svc, mgr = _async()
    assert await svc.never() == "never"
    assert svc.seen == [("never-active", False)]
    assert mgr.calls == []

    with pytest.raises(IllegalTransactionStateError):
        await svc.required_then_never()
    assert mgr.calls == [("begin", "REQUIRED"), ("rollback", "tx1")]
    assert not TSM.is_active()
