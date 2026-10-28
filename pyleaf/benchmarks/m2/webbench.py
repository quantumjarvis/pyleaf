"""Shared helpers for the M2 web benchmarks.

The ratio benchmarks (controller overhead, advice dispatch, middleware
hop) compare a pyleaf arm measured by the ``benchmark`` fixture against a
raw-FastAPI arm timed manually — pytest-benchmark allows only one
``benchmark()`` call per test, so the second arm follows the m1
convention of ``test_unmatched_bean_call_matches_direct``: median of
timed batches.

``asgi_get`` drives an ASGI app directly (no httpx/portal) for the
middleware test, where the delta of interest is per-request middleware
cost and TestClient's ~800 us of transport noise would drown it.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Awaitable, Callable
from typing import Any

BATCHES = 15
CALLS_PER_BATCH = 30


def median_per_call(
    fn: Callable[[], Any],
    batches: int = BATCHES,
    calls: int = CALLS_PER_BATCH,
) -> float:
    """Median seconds-per-call of ``fn`` over ``batches`` timed batches."""
    samples: list[float] = []
    for _ in range(batches):
        start = time.perf_counter()
        for _ in range(calls):
            fn()
        samples.append((time.perf_counter() - start) / calls)
    return statistics.median(samples)


def http_scope(path: str = "/") -> dict[str, Any]:
    """A minimal GET ``http`` ASGI scope for ``path``."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }


async def asgi_get(app: Callable[..., Awaitable[None]], path: str = "/") -> list[dict[str, Any]]:
    """Drive one GET request through ``app`` at the ASGI level."""
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(http_scope(path), receive, send)
    return messages
