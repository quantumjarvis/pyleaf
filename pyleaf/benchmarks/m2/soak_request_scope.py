"""M2 memory soak: 100k sequential requests through RequestScopeMiddleware.

Every request activates a fresh ``RequestScope``, instantiates the
request-scoped bean, and runs its ``@PreDestroy`` on scope close. If
contextvar scopes leaked instances or teardown callbacks, RSS would grow
linearly; the gate is < 20 MB growth over the run.

RSS is read psutil-free (matching m1's conventions): ``VmRSS`` from
``/proc/self/status`` on Linux (current resident set), falling back to
``getrusage().ru_maxrss`` elsewhere — bytes on macOS, KiB elsewhere.

Standalone script, NOT part of the pytest run (filename does not match
``test_*`` and ``benchmarks/`` is outside ``testpaths``). Run it
directly or via ``scripts/quality_m2.sh``:

    uv run python benchmarks/m2/soak_request_scope.py
"""

from __future__ import annotations

import gc
import itertools
import resource
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyleaf.core import (
    ApplicationContext,
    Component,
    PreDestroy,
    Scope,
)
from pyleaf.web import (
    GetMapping,
    RequestScopeMiddleware,
    RestController,
    register_controllers,
)

REQUESTS = 100_000
SAMPLE_EVERY = 5_000
GROWTH_LIMIT_BYTES = 20 * 1024 * 1024

_counter = itertools.count()


def rss_bytes() -> int:
    """Current RSS in bytes (``VmRSS`` on Linux, ``ru_maxrss`` fallback)."""
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports ru_maxrss in bytes; other platforms in KiB.
    return maxrss if sys.platform == "darwin" else maxrss * 1024


def _client() -> TestClient:
    @Component(scope=Scope.REQUEST)
    class SoakBean:
        def __init__(self) -> None:
            self.ident = next(_counter)
            self.destroyed = False

        @PreDestroy
        def teardown(self) -> None:
            self.destroyed = True

    @RestController("/probe")
    class ProbeController:
        def __init__(self, bean: SoakBean) -> None:
            self.bean = bean

        @GetMapping("")
        def probe(self) -> dict[str, int]:
            return {"id": self.bean.ident}

    ctx = ApplicationContext()
    ctx.register(SoakBean)
    ctx.register(ProbeController)
    ctx.refresh()

    app = FastAPI()
    register_controllers(app, ctx)
    return TestClient(RequestScopeMiddleware(app, ctx))


def main() -> int:
    client = _client()
    assert client.get("/probe").status_code == 200

    samples: list[int] = []
    for request_index in range(1, REQUESTS + 1):
        response = client.get("/probe")
        if response.status_code != 200:
            print(f"FAIL: request {request_index} -> {response.status_code}", file=sys.stderr)
            return 1
        if request_index % SAMPLE_EVERY == 0:
            gc.collect()
            samples.append(rss_bytes())
            print(f"  {request_index:>7,} requests  RSS {samples[-1] / 2**20:.1f} MiB")

    growth = samples[-1] - samples[0]
    peak = max(samples) - samples[0]
    print(
        f"RSS growth over {REQUESTS:,} requests: {growth / 2**20:.1f} MiB "
        f"(peak {peak / 2**20:.1f} MiB, limit {GROWTH_LIMIT_BYTES // 2**20} MiB)"
    )
    if growth > GROWTH_LIMIT_BYTES:
        print("FAIL: request scope leaked memory", file=sys.stderr)
        return 1
    print("PASS: request-scope soak within the growth limit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
