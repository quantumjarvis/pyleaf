#!/usr/bin/env bash
# Perf gate for pyleaf M2 (task 2.Q1, PYL-29).
#
# Runs, in order, failing the build on the first nonzero exit:
#   1. benchmarks/m2    pytest-benchmark suite; every test asserts its own
#                       M2 threshold (controller overhead ratio, request
#                       scope cycle, advice dispatch ratio, /actuator/beans,
#                       @WebMiddleware hop)
#   2. baseline compare medians vs benchmarks/baselines/local.json at 50%
#                       (skipped when the file is absent)
#   3. soak             100k sequential TestClient requests through
#                       RequestScopeMiddleware; RSS growth must stay < 20MB
#
# Usage: bash scripts/quality_m2.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== 1/3 pytest benchmarks/m2 (per-benchmark thresholds) =="
uv run pytest benchmarks/m2 --benchmark-only -q

echo "== 2/3 compare vs benchmarks/baselines/local.json (median:50%) =="
if [ -f benchmarks/baselines/local.json ]; then
    uv run pytest benchmarks/m2 -q \
        --benchmark-compare=benchmarks/baselines/local.json \
        --benchmark-compare-fail=median:50%
else
    echo "skipped (no local baseline committed yet)"
fi

echo "== 3/3 request-scope memory soak (100k requests, <20MB RSS growth) =="
uv run python benchmarks/m2/soak_request_scope.py

echo "ALL M2 PERF GATES PASSED"
