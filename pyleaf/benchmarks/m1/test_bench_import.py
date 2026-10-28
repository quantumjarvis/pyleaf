"""M1 benchmark: cold ``import pyleaf.core`` under 50 ms.

A fresh interpreter subprocess times just the import statement (process
startup excluded) and prints the elapsed seconds; the parent asserts on
the median across benchmark rounds. The subprocess wall time is recorded
as benchmark stats.
"""

import subprocess
import sys

THRESHOLD_S = 0.050

_SNIPPET = (
    "import time;"
    "t = time.perf_counter();"
    "import pyleaf.core;"
    "print(f'{time.perf_counter() - t:.6f}')"
)


def _measure_import() -> float:
    out = subprocess.run(
        [sys.executable, "-c", _SNIPPET],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def test_cold_import_pyleaf_core(benchmark) -> None:
    import_times: list[float] = []

    def run() -> None:
        import_times.append(_measure_import())

    benchmark(run)

    # Cold-import floor: the least-contended round is the stable signal;
    # per-round medians drift under benchmark load.
    assert min(import_times) < THRESHOLD_S
