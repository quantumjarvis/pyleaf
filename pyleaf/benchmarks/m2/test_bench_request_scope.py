"""M2 benchmark: request-scope lifecycle must stay under 50 us.

One cycle = ``RequestScope`` activate + instantiate the request-scoped
bean through the container factory path + deactivate + ``close()``
(running ``@PreDestroy``). This is the 2.2 contract's absolute
threshold: the proxy's per-request machinery must be near-free.
"""

from pyleaf.core import (
    ApplicationContext,
    Component,
    PreDestroy,
    RequestScope,
    Scope,
)

CYCLE_THRESHOLD_S = 50e-6


@Component(scope=Scope.REQUEST)
class TracedRequestBean:
    def __init__(self) -> None:
        self.ident = 0
        self.destroyed = False

    @PreDestroy
    def teardown(self) -> None:
        self.destroyed = True


def test_request_scope_cycle(benchmark) -> None:
    ctx = ApplicationContext()
    ctx.register(TracedRequestBean)
    ctx.refresh()
    proxy = ctx.get(TracedRequestBean)  # RequestScopedProxy; lazy per scope

    def cycle() -> int:
        with RequestScope(ctx):
            proxy.ident += 1
            return proxy.ident

    assert cycle() == 1  # one cycle outside the benchmark first

    benchmark(cycle)
    assert benchmark.stats["median"] < CYCLE_THRESHOLD_S, (
        f"request-scope cycle {benchmark.stats['median'] * 1e6:.1f} us "
        f"exceeds {CYCLE_THRESHOLD_S * 1e6:.0f} us"
    )
