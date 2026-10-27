"""RED acceptance tests for async event listeners (WI 1.6).

``await ctx.publish_async(evt)`` awaits async listeners and calls sync ones
sequentially in listener order; ``ctx.publish(evt)`` schedules async
listeners on the running loop, or runs them via ``asyncio.run`` when there
is no running loop.
"""

import asyncio

from pyleaf.core import ApplicationContext, Component, EventListener, Order


class Tick:
    pass


async def test_publish_async_awaits_async_and_calls_sync_listeners() -> None:
    calls: list[str] = []

    @Component
    class Recorder:
        @EventListener
        @Order(1)
        async def on_async(self, evt: Tick) -> None:
            await asyncio.sleep(0)
            calls.append("async")

        @EventListener
        @Order(2)
        def on_sync(self, evt: Tick) -> None:
            calls.append("sync")

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.refresh()

    await ctx.publish_async(Tick())

    assert calls == ["async", "sync"]


async def test_sync_publish_schedules_async_listeners_on_running_loop() -> None:
    calls: list[str] = []
    done = asyncio.Event()

    @Component
    class Recorder:
        @EventListener
        async def on_tick(self, evt: Tick) -> None:
            calls.append("async")
            done.set()

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.refresh()

    ctx.publish(Tick())  # schedules on this test's running loop
    await asyncio.wait_for(done.wait(), timeout=5)

    assert calls == ["async"]


async def test_publish_async_preserves_mixed_listener_order() -> None:
    """Declaration order (then @Order) applies across sync/async listeners."""
    calls: list[str] = []

    @Component
    class Recorder:
        # Method names chosen so alphabetical order != declaration order.
        @EventListener
        async def z_async(self, evt: Tick) -> None:
            await asyncio.sleep(0)
            calls.append("z-async")

        @EventListener
        def m_sync(self, evt: Tick) -> None:
            calls.append("m-sync")

        @EventListener
        async def a_async(self, evt: Tick) -> None:
            calls.append("a-async")

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.refresh()

    await ctx.publish_async(Tick())

    assert calls == ["z-async", "m-sync", "a-async"]


def test_publish_without_running_loop_runs_async_listeners() -> None:
    calls: list[str] = []

    @Component
    class Recorder:
        @EventListener
        async def on_tick(self, evt: Tick) -> None:
            calls.append("async")

    ctx = ApplicationContext()
    ctx.register(Recorder)
    ctx.refresh()

    ctx.publish(Tick())  # no running loop -> asyncio.run

    assert calls == ["async"]
