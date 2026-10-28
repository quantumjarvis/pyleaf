"""M1 benchmark: per-call cost of the AOP interceptor chain.

Sync chain of three ``@Around`` interceptors must stay under 5 us/call;
the async chain under 15 us/call (measured batch-amortized to factor out
event-loop overhead). A bean matched by no pointcut must cost the same as
a direct method call.
"""

import asyncio
import time
from typing import Any

from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Component,
    Invocation,
    Order,
    bean_type,
)

SYNC_THRESHOLD_S = 5e-6
ASYNC_THRESHOLD_S = 15e-6
ASYNC_BATCH = 200
DIRECT_BATCHES = 20
DIRECT_CALLS = 20_000


@Component
class SyncTarget:
    def work(self, x: int) -> int:
        return x + 1


@Component
class AsyncTarget:
    async def work(self, x: int) -> int:
        return x + 1


@Component
class UnmatchedTarget:
    def work(self, x: int) -> int:
        return x + 1


def _sync_wrap(self: Any, inv: Invocation) -> Any:
    return inv.proceed()


async def _async_wrap(self: Any, inv: Invocation) -> Any:
    return await inv.proceed()


def _make_aspect(name: str, order: int, pointcut_target: type, wrap: Any) -> type:
    aspect = type(
        name,
        (),
        {"__module__": __name__, "wrap": Around(bean_type(pointcut_target))(wrap)},
    )
    return Order(order)(Aspect(aspect))


SyncAspect1 = _make_aspect("SyncAspect1", 1, SyncTarget, _sync_wrap)
SyncAspect2 = _make_aspect("SyncAspect2", 2, SyncTarget, _sync_wrap)
SyncAspect3 = _make_aspect("SyncAspect3", 3, SyncTarget, _sync_wrap)
AsyncAspect1 = _make_aspect("AsyncAspect1", 1, AsyncTarget, _async_wrap)
AsyncAspect2 = _make_aspect("AsyncAspect2", 2, AsyncTarget, _async_wrap)
AsyncAspect3 = _make_aspect("AsyncAspect3", 3, AsyncTarget, _async_wrap)


def _context(*classes: type) -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def test_aop_sync_chain_of_3(benchmark) -> None:
    ctx = _context(SyncAspect1, SyncAspect2, SyncAspect3, SyncTarget)
    bean = ctx.get(SyncTarget)

    result = benchmark(bean.work, 1)

    assert result == 2
    assert benchmark.stats["median"] < SYNC_THRESHOLD_S


def test_aop_async_chain_of_3(benchmark) -> None:
    ctx = _context(AsyncAspect1, AsyncAspect2, AsyncAspect3, AsyncTarget)
    bean = ctx.get(AsyncTarget)
    loop = asyncio.new_event_loop()
    try:

        async def batch() -> int:
            total = 0
            for _ in range(ASYNC_BATCH):
                total += await bean.work(1)
            return total

        assert loop.run_until_complete(batch()) == 2 * ASYNC_BATCH
        benchmark(lambda: loop.run_until_complete(batch()))

        per_call = benchmark.stats["median"] / ASYNC_BATCH
        assert per_call < ASYNC_THRESHOLD_S
    finally:
        loop.close()


def test_unmatched_bean_call_matches_direct(benchmark) -> None:
    """A bean whose methods match no pointcut keeps plain bound-method cost."""
    ctx = _context(SyncAspect1, SyncAspect2, SyncAspect3, SyncTarget, UnmatchedTarget)
    bean = ctx.get(UnmatchedTarget)
    direct = UnmatchedTarget()

    def direct_call() -> None:
        for _ in range(DIRECT_CALLS):
            direct.work(1)

    def timed(fn: Any) -> float:
        start = time.perf_counter()
        fn()
        return time.perf_counter() - start

    direct_s = min(timed(direct_call) for _ in range(DIRECT_BATCHES)) / DIRECT_CALLS

    assert benchmark(bean.work, 1) == 2
    median = benchmark.stats["median"]
    assert median < 2 * direct_s or median - direct_s < 0.5e-6
