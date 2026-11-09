import sys
from pathlib import Path

M3_DIR = Path(__file__).resolve().parent
M2_DIR = M3_DIR.parent / "m2"

# m2's webbench holds median_per_call — the manual-timing helper the
# same-run ratio benchmarks use for their non-benchmark() arm (only one
# benchmark() call is allowed per test).
sys.path.insert(0, str(M2_DIR))
