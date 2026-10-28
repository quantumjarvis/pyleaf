"""M1 benchmark: ``ctx.get()`` on a resolved singleton must be near-free."""

from pyleaf.core import ApplicationContext, Component

GET_THRESHOLD_S = 2e-6


@Component
class GetTarget:
    pass


def test_get_resolved_singleton(benchmark) -> None:
    ctx = ApplicationContext()
    ctx.register(GetTarget)
    ctx.refresh()
    bean = ctx.get(GetTarget)  # warm the resolved-definition cache

    result = benchmark(ctx.get, GetTarget)

    assert result is bean
    assert benchmark.stats["median"] < GET_THRESHOLD_S
