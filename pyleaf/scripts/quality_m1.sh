#!/usr/bin/env bash
# Quality gate for pyleaf M1+M2 (tasks 1.Q2/PYL-19, PYL-30).
#
# Runs, in order, failing the build on the first nonzero exit:
#   1. pip-audit        dependency vulnerability scan
#   2. bandit           static security scan over packages/*/src
#                       (covers M1 and M2 packages alike)
#   3. ruff check       lint incl. the flake8-bandit S rules
#   4. coverage gates   M1: resolver/aop/conditions/autoconfigure >= 90%
#                       M2: every pyleaf.web and pyleaf.actuator module >= 90%
#                       M3: pyleaf.tx, pyleaf.data, pyleaf.security and the
#                           sqlalchemy starter managers >= 90%
#                       TOTAL >= 80%
#   5. mutmut           mutation testing of pyleaf.core.conditions and
#                       pyleaf.tx.template (opt-in via --mutmut; runtime is
#                       minutes, so the default is to echo the command)
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
    --cov=pyleaf.web --cov=pyleaf.actuator \
    --cov=pyleaf.tx --cov=pyleaf.data --cov=pyleaf.security \
    --cov-report=term-missing

# coverage.py has no global per-file fail-under; gate each file separately.
gate_file() {
    local include="$1" name="$2"
    if ! uv run coverage report --include="$include" --fail-under=90; then
        echo "COVERAGE GATE FAILED: $name is below 90%" >&2
        exit 1
    fi
}

# M1 gated files.
gate_file "*/pyleaf/core/resolver.py" "pyleaf.core.resolver"
gate_file "*/pyleaf/core/aop.py" "pyleaf.core.aop"
gate_file "*/pyleaf/core/conditions.py" "pyleaf.core.conditions"
gate_file "*/pyleaf/boot/autoconfigure.py" "pyleaf.boot.autoconfigure"

# M2 gated files (PYL-30): every web/actuator module must hold >= 90%.
gate_file "*/pyleaf/web/advice.py" "pyleaf.web.advice"
gate_file "*/pyleaf/web/autoconfigure.py" "pyleaf.web.autoconfigure"
gate_file "*/pyleaf/web/customizer.py" "pyleaf.web.customizer"
gate_file "*/pyleaf/web/middleware.py" "pyleaf.web.middleware"
gate_file "*/pyleaf/web/router.py" "pyleaf.web.router"
gate_file "*/pyleaf/web/stereotypes.py" "pyleaf.web.stereotypes"
gate_file "*/pyleaf/actuator/autoconfigure.py" "pyleaf.actuator.autoconfigure"
gate_file "*/pyleaf/actuator/endpoints.py" "pyleaf.actuator.endpoints"
gate_file "*/pyleaf/actuator/health.py" "pyleaf.actuator.health"

# M3 gated files (PYL-42): every tx/data/security module plus the
# sqlalchemy starter's autoconfigure + transaction managers must hold
# >= 90%.
# pyleaf.tx
gate_file "*/pyleaf/tx/autoconfigure.py" "pyleaf.tx.autoconfigure"
gate_file "*/pyleaf/tx/definition.py" "pyleaf.tx.definition"
gate_file "*/pyleaf/tx/errors.py" "pyleaf.tx.errors"
gate_file "*/pyleaf/tx/events.py" "pyleaf.tx.events"
gate_file "*/pyleaf/tx/interceptor.py" "pyleaf.tx.interceptor"
gate_file "*/pyleaf/tx/manager.py" "pyleaf.tx.manager"
gate_file "*/pyleaf/tx/status.py" "pyleaf.tx.status"
gate_file "*/pyleaf/tx/sync.py" "pyleaf.tx.sync"
gate_file "*/pyleaf/tx/template.py" "pyleaf.tx.template"
gate_file "*/pyleaf/tx/transactional.py" "pyleaf.tx.transactional"
# pyleaf.starter.sqlalchemy
gate_file "*/pyleaf/starter/sqlalchemy/autoconfigure.py" \
    "pyleaf.starter.sqlalchemy.autoconfigure"
gate_file "*/pyleaf/starter/sqlalchemy/tx.py" \
    "pyleaf.starter.sqlalchemy.tx"
# pyleaf.data
gate_file "*/pyleaf/data/autoconfig.py" "pyleaf.data.autoconfig"
gate_file "*/pyleaf/data/migrations.py" "pyleaf.data.migrations"
gate_file "*/pyleaf/data/paging.py" "pyleaf.data.paging"
gate_file "*/pyleaf/data/query.py" "pyleaf.data.query"
gate_file "*/pyleaf/data/repository.py" "pyleaf.data.repository"
# pyleaf.security
gate_file "*/pyleaf/security/autoconfig.py" "pyleaf.security.autoconfig"
gate_file "*/pyleaf/security/context.py" "pyleaf.security.context"
gate_file "*/pyleaf/security/core.py" "pyleaf.security.core"
gate_file "*/pyleaf/security/errors.py" "pyleaf.security.errors"
gate_file "*/pyleaf/security/expression.py" "pyleaf.security.expression"
gate_file "*/pyleaf/security/jwt.py" "pyleaf.security.jwt"
gate_file "*/pyleaf/security/method.py" "pyleaf.security.method"
gate_file "*/pyleaf/security/password.py" "pyleaf.security.password"
gate_file "*/pyleaf/security/web.py" "pyleaf.security.web"

if ! uv run coverage report --fail-under=80; then
    echo "COVERAGE GATE FAILED: TOTAL is below 80%" >&2
    exit 1
fi

echo "== 5/5 mutmut: mutation testing (core conditions + tx template) =="
if [[ "${1:-}" == "--mutmut" ]]; then
    # mutmut 3.x puts mutants/src on sys.path and removes ./src; running from
    # the package dir is what swaps in the mutated copy of pyleaf.core.
    (cd packages/pyleaf-core && uv run mutmut run)
    # PYL-42: same trick for pyleaf.tx — [tool.mutmut] in its pyproject
    # scopes mutation to src/pyleaf/tx/template.py (the propagation
    # state machine).
    (cd packages/pyleaf-tx && uv run mutmut run)
else
    echo "skipped (CI-optional; runtime is minutes). Run manually with:"
    echo "    cd packages/pyleaf-core && uv run mutmut run"
    echo "    cd packages/pyleaf-tx && uv run mutmut run"
fi

echo "ALL QUALITY GATES PASSED"
