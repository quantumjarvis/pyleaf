"""M3 benchmark: @Transactional REQUIRED join must stay near-free.

An advised no-op method joining an already-bound transaction costs the
AOP wrapper + the boundary's plan/begin/exit — no commit, no
synchronizations, no TSM writes. Thresholds (design-3.2): < 20 us sync,
< 40 us async (batch-amortized like the M1 async chain).
"""

import asyncio
from typing import Any

from pyleaf.core import ApplicationContext, Component
from pyleaf.tx import Transactional, TransactionStatus, TxAutoConfiguration
from pyleaf.tx import (
    TransactionSynchronizationManager as TSM,
)

SYNC_THRESHOLD_S = 20e-6
ASYNC_THRESHOLD_S = 40e-6
ASYNC_BATCH = 200


class _JoinStatus(TransactionStatus):
    """Awaitable status so the noop manager serves both sync and async."""

    def __await__(self) -> Any:
        yield from ()
        return self


@Component
class NoopManager:
    """Join-only manager: ``begin`` returns the already-bound status."""

    def begin(self, definition: Any) -> Any:
        return TSM.get_resource(self)

    def commit(self, status: Any) -> None:
        pass

    def rollback(self, status: Any) -> None:
        pass


@Component
class BenchSyncService:
    @Transactional
    def noop(self) -> None:
        pass


@Component
class BenchAsyncService:
    @Transactional
    async def noop(self) -> None:
        pass


def _ctx(*classes: type) -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def test_bench_transactional_noop_sync(benchmark) -> None:
    ctx = _ctx(NoopManager, BenchSyncService, TxAutoConfiguration)
    svc = ctx.get(BenchSyncService)
    mgr = ctx.get(NoopManager)
    # An active context with a bound (join-able) status: REQUIRED joins it.
    TSM.init()
    TSM.bind_resource(mgr, _JoinStatus(is_new=False))
    try:
        svc.noop()  # warm the manager-resolution cache
        benchmark(svc.noop)
        assert benchmark.stats["median"] < SYNC_THRESHOLD_S, (
            f"transactional noop join {benchmark.stats['median'] * 1e6:.2f} us"
            f" exceeds {SYNC_THRESHOLD_S * 1e6:.0f} us"
        )
    finally:
        TSM.clear()


def test_bench_transactional_noop_async(benchmark) -> None:
    ctx = _ctx(NoopManager, BenchAsyncService, TxAutoConfiguration)
    svc = ctx.get(BenchAsyncService)
    mgr = ctx.get(NoopManager)
    loop = asyncio.new_event_loop()
    TSM.init()
    TSM.bind_resource(mgr, _JoinStatus(is_new=False))
    try:

        async def batch() -> int:
            total = 0
            for _ in range(ASYNC_BATCH):
                await svc.noop()
                total += 1
            return total

        assert loop.run_until_complete(batch()) == ASYNC_BATCH
        benchmark(lambda: loop.run_until_complete(batch()))
        per_call = benchmark.stats["median"] / ASYNC_BATCH
        assert per_call < ASYNC_THRESHOLD_S, (
            f"transactional async noop join {per_call * 1e6:.2f} us"
            f" exceeds {ASYNC_THRESHOLD_S * 1e6:.0f} us"
        )
    finally:
        TSM.clear()
        loop.close()
