#!/usr/bin/env bash
# Quality gate for pyleaf M1 (task 1.Q2, PYL-19).
#
# Runs, in order, failing the build on the first nonzero exit:
#   1. pip-audit        dependency vulnerability scan
#   2. bandit           static security scan over packages/*/src
#   3. ruff check       lint incl. the flake8-bandit S rules
#   4. coverage gates   resolver/aop/conditions/autoconfigure >= 90% each,
#                       TOTAL >= 80%
#   5. mutmut           mutation testing of pyleaf.core.conditions
#                       (opt-in via --mutmut; runtime is minutes, so the
#                       default is to echo the exact command)
#
# Usage: bash scripts/quality_m1.sh [--mutmut]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== 1/5 pip-audit: dependency vulnerability scan =="
uv run pip-audit

echo "== 2/5 bandit: static security scan of packages/*/src =="
uv run bandit -r packages/*/src

echo "== 3/5 ruff: lint (includes flake8-bandit S rules) =="
uv run ruff check .

echo "== 4/5 coverage gates =="
# -p no:pyleaf is REQUIRED: the pyleaf-test pytest11 plugin imports
# pyleaf.core at plugin load, before coverage tracing starts, which would
# silently under-report module coverage.
uv run pytest -p no:pyleaf packages/*/tests -q \
    --cov=pyleaf.core --cov=pyleaf.boot --cov=pyleaf.starter \
    --cov-report=term-missing

# coverage.py has no global per-file fail-under; gate each file separately.
gate_file() {
    local include="$1" name="$2"
    if ! uv run coverage report --include="$include" --fail-under=90; then
        echo "COVERAGE GATE FAILED: $name is below 90%" >&2
        exit 1
    fi
}

gate_file "*/pyleaf/core/resolver.py" "pyleaf.core.resolver"
gate_file "*/pyleaf/core/aop.py" "pyleaf.core.aop"
gate_file "*/pyleaf/core/conditions.py" "pyleaf.core.conditions"
gate_file "*/pyleaf/boot/autoconfigure.py" "pyleaf.boot.autoconfigure"

if ! uv run coverage report --fail-under=80; then
    echo "COVERAGE GATE FAILED: TOTAL is below 80%" >&2
    exit 1
fi

echo "== 5/5 mutmut: mutation testing of pyleaf/core/conditions.py =="
if [[ "${1:-}" == "--mutmut" ]]; then
    # mutmut 3.x puts mutants/src on sys.path and removes ./src; running from
    # the package dir is what swaps in the mutated copy of pyleaf.core.
    (cd packages/pyleaf-core && uv run mutmut run)
else
    echo "skipped (CI-optional; runtime is minutes). Run manually with:"
    echo "    cd packages/pyleaf-core && uv run mutmut run"
fi

echo "ALL QUALITY GATES PASSED"
