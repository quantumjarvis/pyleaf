"""M1 benchmark: memory footprint of 1,000 singleton beans.

tracemalloc peak is used instead of RSS (psutil-free, deterministic):
the delta for building a context and instantiating 1,000 singletons must
stay under 5 MiB. The benchmark fixture records build timing alongside.
"""

import tracemalloc

from benchgen import make_chain_classes
from pyleaf.core import ApplicationContext

BEAN_COUNT = 1000
THRESHOLD_BYTES = 5 * 1024 * 1024

CLASSES = make_chain_classes(BEAN_COUNT, lazy=False, prefix="Mem")


def _build() -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in CLASSES:
        ctx.register(cls)
    ctx.refresh(eager=True)
    return ctx


def test_memory_1000_singletons(benchmark) -> None:
    tracemalloc.start()
    try:
        ctx = _build()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert ctx.get_bean("memL2_0") is not None
    assert peak < THRESHOLD_BYTES

    benchmark(_build)
