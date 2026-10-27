"""RED acceptance tests for async interception (WI 1.4).

Async advice may wrap async target methods; any sync/async mismatch between
a matching advice and the target method is a PyleafError at refresh().
"""

from typing import Any

import pytest
from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Component,
    Invocation,
    PyleafError,
    bean_type,
)


async def test_async_advice_intercepts_async_method() -> None:
    calls: list[str] = []

    @Component
    class AsyncService:
        async def work(self) -> str:
            calls.append("method")
            return "async-done"

    @Aspect
    @Component
    class AsyncAspect:
        @Around(bean_type(AsyncService))
        async def wrap(self, inv: Invocation) -> Any:
            calls.append("in")
            result = await inv.proceed()
            calls.append("out")
            return result

    ctx = ApplicationContext()
    ctx.register(AsyncAspect)
    ctx.register(AsyncService)
    ctx.refresh()

    bean = ctx.get(AsyncService)
    assert await bean.work() == "async-done"
    assert calls == ["in", "method", "out"]


def test_sync_advice_on_async_method_fails_at_refresh() -> None:
    @Component
    class AsyncService:
        async def work(self) -> str:
            return "never"

    @Aspect
    @Component
    class SyncAspect:
        @Around(bean_type(AsyncService))
        def wrap(self, inv: Invocation) -> Any:
            return inv.proceed()

    ctx = ApplicationContext()
    ctx.register(SyncAspect)
    ctx.register(AsyncService)

    with pytest.raises(PyleafError) as exc_info:
        ctx.refresh()
    # Error names the offending bean method and the aspect.
    assert "work" in str(exc_info.value)
    assert "SyncAspect" in str(exc_info.value)


def test_async_advice_on_sync_method_fails_at_refresh() -> None:
    @Component
    class SyncService:
        def work(self) -> str:
            return "never"

    @Aspect
    @Component
    class AsyncAspect:
        @Around(bean_type(SyncService))
        async def wrap(self, inv: Invocation) -> Any:
            return await inv.proceed()

    ctx = ApplicationContext()
    ctx.register(AsyncAspect)
    ctx.register(SyncService)

    with pytest.raises(PyleafError) as exc_info:
        ctx.refresh()
    assert "work" in str(exc_info.value)
    assert "AsyncAspect" in str(exc_info.value)
