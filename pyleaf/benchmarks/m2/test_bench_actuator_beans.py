"""M2 benchmark: /actuator/beans on a 200-definition context < 50 ms.

``beans_payload`` enumerates definitions and resolves each def's
``dep_specs`` against the registry — an O(defs^2) walk — without
instantiating anything. PYL-132's perf clause caps the round-trip at
50 ms. Bean graphs come from m1's ``benchgen`` chain generator.
"""

from benchgen import make_chain_classes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyleaf.actuator.autoconfigure import ActuatorCustomizer
from pyleaf.core import ApplicationContext

BEAN_DEFS = 200
THRESHOLD_S = 50e-3
WARMUP_CALLS = 5

CLASSES = make_chain_classes(BEAN_DEFS, prefix="BeansBench")


def test_actuator_beans_200_defs(benchmark) -> None:
    ctx = ApplicationContext()
    for cls in CLASSES:
        ctx.register(cls)
    ctx.refresh()

    app = FastAPI()
    ActuatorCustomizer(ctx).customize(app)
    client = TestClient(app)

    response = client.get("/actuator/beans")
    assert response.status_code == 200
    assert len(response.json()) >= BEAN_DEFS
    for _ in range(WARMUP_CALLS):
        client.get("/actuator/beans")

    benchmark(client.get, "/actuator/beans")
    assert benchmark.stats["median"] < THRESHOLD_S, (
        f"/actuator/beans {benchmark.stats['median'] * 1e3:.1f} ms "
        f"exceeds {THRESHOLD_S * 1e3:.0f} ms on {BEAN_DEFS} definitions"
    )
