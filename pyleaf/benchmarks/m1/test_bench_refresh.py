"""M1 benchmark: context refresh over 1,000 bean definitions.

Lazy refresh only registers and validates the graph; eager refresh also
pre-instantiates every singleton (3-deep dependency chains).
"""

from benchgen import make_chain_classes
from pyleaf.core import ApplicationContext

BEAN_COUNT = 1000
LAZY_THRESHOLD_S = 0.300
EAGER_THRESHOLD_S = 1.0

LAZY_CLASSES = make_chain_classes(BEAN_COUNT, lazy=True, prefix="BenchLazy")
EAGER_CLASSES = make_chain_classes(BEAN_COUNT, lazy=False, prefix="BenchEager")


def _build_context(classes: list[type], *, eager: bool) -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in classes:
        ctx.register(cls)
    ctx.refresh(eager=eager)
    return ctx


def test_refresh_lazy_1000_definitions(benchmark) -> None:
    """Registration + condition eval + AOP/listener tables + cycle detection."""
    ctx = benchmark(_build_context, LAZY_CLASSES, eager=False)
    # Lazy refresh instantiates nothing; forced singletons are framework
    # internals only. Resolving one bean proves the graph is usable.
    assert ctx.get_bean("benchLazyL2_0") is not None
    assert benchmark.stats["median"] < LAZY_THRESHOLD_S


def test_refresh_eager_1000_definitions(benchmark) -> None:
    """Eager refresh additionally instantiates all 1,000 singletons."""
    ctx = benchmark(_build_context, EAGER_CLASSES, eager=True)
    assert ctx.get_bean("benchEagerL2_0") is not None
    assert benchmark.stats["median"] < EAGER_THRESHOLD_S
