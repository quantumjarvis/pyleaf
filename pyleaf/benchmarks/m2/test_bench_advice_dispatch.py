"""M2 benchmark: @ControllerAdvice dispatch vs a FastAPI exception handler.

A route that raises is served (a) by a plain FastAPI route with
``app.add_exception_handler`` and (b) by a ``@RestController`` whose
exception is rendered by a ``@ControllerAdvice`` bean via
``register_advice``. Both arms return the same 422 JSON body and are
measured as TestClient round-trips. Threshold: the advice arm's p50 must
stay within 25% of the raw arm's median — a measured ratio, not an
absolute µs.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pyleaf.core import ApplicationContext
from pyleaf.web import (
    ControllerAdvice,
    ExceptionHandler,
    GetMapping,
    RestController,
    register_advice,
    register_controllers,
)
from webbench import median_per_call

OVERHEAD_RATIO_LIMIT = 1.25
WARMUP_CALLS = 50


class BenchBoom(Exception):
    """Domain error raised by the throwing routes."""


def _raw_client() -> TestClient:
    app = FastAPI()

    async def handle_boom(request: Request, exc: BenchBoom) -> JSONResponse:
        return JSONResponse({"handled": "raw"}, status_code=422)

    app.add_exception_handler(BenchBoom, handle_boom)

    @app.get("/boom")
    def boom() -> None:
        raise BenchBoom("boom")

    return TestClient(app, raise_server_exceptions=False)


def _pyleaf_client() -> TestClient:
    @ControllerAdvice
    class BenchAdvice:
        @ExceptionHandler(BenchBoom, status_code=422)
        async def on_boom(self, exc: BenchBoom) -> dict[str, str]:
            return {"handled": "advice"}

    @RestController("/ctl")
    class BoomController:
        @GetMapping("/boom")
        def boom(self) -> None:
            raise BenchBoom("boom")

    ctx = ApplicationContext()
    ctx.register(BenchAdvice)
    ctx.register(BoomController)
    ctx.refresh()

    app = FastAPI()
    register_controllers(app, ctx)
    register_advice(app, ctx)
    return TestClient(app, raise_server_exceptions=False)


def test_advice_dispatch_vs_fastapi_handler(benchmark) -> None:
    raw_client = _raw_client()
    pyleaf_client = _pyleaf_client()
    assert raw_client.get("/boom").json() == {"handled": "raw"}
    assert pyleaf_client.get("/ctl/boom").json() == {"handled": "advice"}
    for _ in range(WARMUP_CALLS):
        raw_client.get("/boom")
        pyleaf_client.get("/ctl/boom")

    raw_s = median_per_call(lambda: raw_client.get("/boom"))

    response = benchmark(pyleaf_client.get, "/ctl/boom")
    assert response.status_code == 422

    pyleaf_s = benchmark.stats["median"]
    benchmark.extra_info["raw_median_s"] = raw_s
    benchmark.extra_info["overhead_ratio"] = pyleaf_s / raw_s
    assert pyleaf_s < raw_s * OVERHEAD_RATIO_LIMIT, (
        f"advice dispatch {pyleaf_s / raw_s:.3f}x exceeds "
        f"{OVERHEAD_RATIO_LIMIT:.2f}x (pyleaf {pyleaf_s * 1e6:.1f} us "
        f"vs raw {raw_s * 1e6:.1f} us)"
    )
