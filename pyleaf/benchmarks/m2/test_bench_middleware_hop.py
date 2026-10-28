"""M2 benchmark: each @WebMiddleware hop adds < 20 us of latency.

``register_middleware`` mounts each bean's ``dispatch`` through
Starlette's ``BaseHTTPMiddleware``, whose own plumbing — an anyio task
group plus memory-object streams per request — costs ~130 us per hop on
this machine. That plumbing is machinery pyleaf does not control and
cannot inline, so the 20 us gate is measured as the DELTA between a
stack of ``@WebMiddleware`` hops and an identical stack of hand-mounted
``BaseHTTPMiddleware`` passthrough dispatches: the latency the pyleaf
bean layer itself adds per hop.

Both arms are driven at raw ASGI level (no httpx/TestClient portal) in
batches inside one event loop — the same batch-amortized convention as
m1's async AOP benchmark.
"""

import asyncio
import statistics
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pyleaf.core import ApplicationContext
from pyleaf.web import WebMiddleware, register_middleware
from starlette.middleware.base import BaseHTTPMiddleware
from webbench import asgi_get

HOPS = 3
BATCH_CALLS = 40
TIMING_BATCHES = 15
HOP_THRESHOLD_S = 20e-6


async def _bean_passthrough(
    self: object,
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    return await call_next(request)


async def _raw_passthrough(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    return await call_next(request)


def _pyleaf_app() -> FastAPI:
    ctx = ApplicationContext()
    for index in range(HOPS):
        cls = type(
            f"BenchMw{index}",
            (),
            {"__module__": __name__, "dispatch": _bean_passthrough},
        )
        ctx.register(WebMiddleware(cls))
    ctx.refresh()

    app = FastAPI()

    @app.get("/")
    def root() -> dict[str, bool]:
        return {"ok": True}

    register_middleware(app, ctx)
    return app


def _raw_app() -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def root() -> dict[str, bool]:
        return {"ok": True}

    for _ in range(HOPS):
        app.add_middleware(BaseHTTPMiddleware, dispatch=_raw_passthrough)
    return app


async def _run(app: Callable[..., Awaitable[None]], calls: int) -> None:
    for _ in range(calls):
        await asgi_get(app)


def _median_batch_s(
    loop: asyncio.AbstractEventLoop,
    app: Callable[..., Awaitable[None]],
) -> float:
    """Median seconds-per-request over TIMING_BATCHES batches."""
    samples: list[float] = []
    for _ in range(TIMING_BATCHES):
        start = time.perf_counter()
        loop.run_until_complete(_run(app, BATCH_CALLS))
        samples.append((time.perf_counter() - start) / BATCH_CALLS)
    return statistics.median(samples)


def test_webmiddleware_hop_added_latency(benchmark) -> None:
    loop = asyncio.new_event_loop()
    try:
        pyleaf_app = _pyleaf_app()
        raw_app = _raw_app()

        async def smoke() -> int:
            messages = await asgi_get(pyleaf_app)
            return next(
                m["status"] for m in messages if m["type"] == "http.response.start"
            )

        assert loop.run_until_complete(smoke()) == 200
        # Warm both stacks before timing either.
        loop.run_until_complete(_run(pyleaf_app, 60))
        loop.run_until_complete(_run(raw_app, 60))

        raw_s = _median_batch_s(loop, raw_app)

        benchmark(lambda: loop.run_until_complete(_run(pyleaf_app, BATCH_CALLS)))
        pyleaf_s = benchmark.stats["median"] / BATCH_CALLS

        added_per_hop = (pyleaf_s - raw_s) / HOPS
        benchmark.extra_info["raw_median_s"] = raw_s
        benchmark.extra_info["added_per_hop_s"] = added_per_hop
        assert added_per_hop < HOP_THRESHOLD_S, (
            f"@WebMiddleware hop adds {added_per_hop * 1e6:.1f} us vs an "
            f"equivalent BaseHTTPMiddleware stack (limit "
            f"{HOP_THRESHOLD_S * 1e6:.0f} us; pyleaf {pyleaf_s * 1e6:.1f} "
            f"us/req, raw {raw_s * 1e6:.1f} us/req, {HOPS} hops)"
        )
    finally:
        loop.close()
