"""M2 benchmark (headline): @RestController overhead vs raw FastAPI.

The same JSON route is served (a) by a plain FastAPI function and (b) by
a pyleaf ``@RestController`` bean mounted via ``register_controllers`` —
both on ONE app and ONE TestClient, so the only difference on the hot
path is the container-produced bound method endpoint. Threshold: the
pyleaf arm's p50 must stay within 10% of the raw arm's median — a
measured ratio, never a hardcoded absolute µs (runners vary).
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyleaf.core import ApplicationContext
from pyleaf.web import GetMapping, RestController, register_controllers
from webbench import median_per_call

OVERHEAD_RATIO_LIMIT = 1.10
WARMUP_CALLS = 100


def _app() -> FastAPI:
    @RestController("/ctl")
    class PingController:
        @GetMapping("/ping")
        def ping(self) -> dict[str, bool]:
            return {"ok": True}

    ctx = ApplicationContext()
    ctx.register(PingController)
    ctx.refresh()

    app = FastAPI()

    @app.get("/raw/ping")
    def raw_ping() -> dict[str, bool]:
        return {"ok": True}

    register_controllers(app, ctx)
    return app


def test_controller_overhead_vs_raw_fastapi(benchmark) -> None:
    client = TestClient(_app())
    # Warm routing, response serialization and the TestClient portal.
    for _ in range(WARMUP_CALLS):
        client.get("/raw/ping")
        client.get("/ctl/ping")

    raw_s = median_per_call(lambda: client.get("/raw/ping"))

    response = benchmark(client.get, "/ctl/ping")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    pyleaf_s = benchmark.stats["median"]
    benchmark.extra_info["raw_median_s"] = raw_s
    benchmark.extra_info["overhead_ratio"] = pyleaf_s / raw_s
    assert pyleaf_s < raw_s * OVERHEAD_RATIO_LIMIT, (
        f"controller overhead {pyleaf_s / raw_s:.3f}x exceeds "
        f"{OVERHEAD_RATIO_LIMIT:.2f}x (pyleaf {pyleaf_s * 1e6:.1f} us "
        f"vs raw {raw_s * 1e6:.1f} us)"
    )
