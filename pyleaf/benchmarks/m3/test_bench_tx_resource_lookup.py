"""M3 benchmark: tx resource lookup must stay under 1 us.

One lookup = ``TransactionSynchronizationManager.get_resource`` on an
active context: a single ContextVar.get + dict.get. The 3.1 contract
requires it be near-free — it runs on every transactional resource
access.
"""

from pyleaf.tx import TransactionSynchronizationManager as TSM

LOOKUP_THRESHOLD_S = 1e-6


def test_tx_resource_lookup(benchmark) -> None:
    TSM.init()
    resource = object()
    TSM.bind_resource("ds", resource)

    def lookup() -> object:
        return TSM.get_resource("ds")

    assert lookup() is resource  # warm the path outside the benchmark
    benchmark(lookup)
    TSM.clear()
    assert benchmark.stats["median"] < LOOKUP_THRESHOLD_S, (
        f"tx resource lookup {benchmark.stats['median'] * 1e6:.2f} us "
        f"exceeds {LOOKUP_THRESHOLD_S * 1e6:.0f} us"
    )
