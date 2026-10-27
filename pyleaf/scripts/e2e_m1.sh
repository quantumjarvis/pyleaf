#!/usr/bin/env bash
# E2E contract for pyleaf M1 (WI 1.9, PYL-15).
#
# Fresh venv -> editable-install core/boot/starter -> four assertions:
#   1. `python -m hello` boots; the starter creates an Engine bean.
#   2. A user-provided Engine bean makes the starter's engine def back off
#      with an @ConditionalOnMissingBean reason in the condition report.
#   3. With sqlalchemy uninstalled the starter drops its defs with an
#      @ConditionalOnClass "is not importable" reason (no import errors).
#   4. The counting aspect records internal() == 2 interceptions for a
#      public() -> self.internal() call plus a direct internal() call.
#
# Exit 0 only when all four assertions pass.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.e2e-venv"
PYTHON="$VENV/bin/python"

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; exit 1; }

echo "== creating fresh venv at $VENV =="
rm -rf "$VENV"
uv venv "$VENV" --quiet
uv pip install --python "$PYTHON" --quiet \
    -e packages/pyleaf-core \
    -e packages/pyleaf-boot \
    -e packages/pyleaf-starter-sqlalchemy

# --- Assertion 1: `python -m hello` boots and an Engine bean exists --------
echo "== assertion 1: engine created via autoconfigure entry point =="
OUT="$(cd examples && "$PYTHON" -m hello)"
echo "$OUT"
echo "$OUT" | grep -q "Hello from pyleaf!" \
    || fail "greeting not printed"
echo "$OUT" | grep -q "ENGINE_OK" \
    || fail "engine bean missing (expected ENGINE_OK marker)"
pass "1: Engine bean created by starter auto-configuration"

# --- Assertion 2: user Engine bean -> OnMissingBean backoff ----------------
echo "== assertion 2: user Engine bean satisfies @ConditionalOnMissingBean =="
OUT="$("$PYTHON" - <<'PYEOF'
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from pyleaf.boot import (
    discover_autoconfigs,
    load_environment,
    register_autoconfigs,
)
from pyleaf.core import ApplicationContext, Bean, Configuration


@Configuration
class UserDatabase:
    @Bean
    def myEngine(self) -> Engine:
        return create_engine("sqlite:///:memory:")


env = load_environment("examples/hello")
ctx = ApplicationContext(environment=env)
ctx.register(UserDatabase)  # user beans register before auto-configs
register_autoconfigs(ctx, discover_autoconfigs())
ctx.refresh()

assert ctx.get(Engine) is ctx.get_bean("myEngine")
for entry in ctx.condition_report.entries:
    reasons = " | ".join(entry.reasons)
    print(f"REPORT {entry.bean_name} matched={entry.matched} :: {reasons}")
PYEOF
)"
echo "$OUT"
echo "$OUT" | grep "^REPORT engine " | grep -q "matched=False" \
    || fail "starter engine def was not dropped"
echo "$OUT" | grep "^REPORT engine " | grep -q "@ConditionalOnMissingBean" \
    || fail "no @ConditionalOnMissingBean reason for engine"
pass "2: user Engine bean -> starter def dropped (OnMissingBean reason)"

# --- Assertion 3: sqlalchemy uninstalled -> OnClass reason ------------------
echo "== assertion 3: sqlalchemy absent -> @ConditionalOnClass unmatched =="
uv pip uninstall --python "$PYTHON" --quiet sqlalchemy

OUT="$("$PYTHON" - <<'PYEOF'
# Deliberately no sqlalchemy imports: the starter module must still load.
from pyleaf.boot import (
    discover_autoconfigs,
    load_environment,
    register_autoconfigs,
)
from pyleaf.core import ApplicationContext

env = load_environment("examples/hello")
ctx = ApplicationContext(environment=env)
register_autoconfigs(ctx, discover_autoconfigs())
ctx.refresh()

for entry in ctx.condition_report.entries:
    reasons = " | ".join(entry.reasons)
    print(f"REPORT {entry.bean_name} matched={entry.matched} :: {reasons}")
PYEOF
)"
echo "$OUT"
for BEAN in sqlAlchemyAutoConfiguration engine sessionfactory; do
    echo "$OUT" | grep "^REPORT ${BEAN} " | grep -q "matched=False" \
        || fail "${BEAN} was not dropped without sqlalchemy"
    echo "$OUT" | grep "^REPORT ${BEAN} " | grep -q "is not importable" \
        || fail "${BEAN} missing @ConditionalOnClass reason"
done
pass "3: sqlalchemy absent -> starter defs dropped (OnClass reason)"

# --- Assertion 4: self-invocation interception -> internal count == 2 -------
echo "== assertion 4: aspect counts self-invoked + direct internal() calls =="
OUT="$(cd examples && "$PYTHON" -m hello)"
echo "$OUT"
echo "$OUT" | grep -q "ASPECT_INTERNAL_COUNT=2" \
    || fail "aspect internal() count != 2"
echo "$OUT" | grep -q "ENGINE_MISSING" \
    || fail "expected graceful ENGINE_MISSING without sqlalchemy"
pass "4: aspect internal() count == 2 (self-invocation intercepted)"

echo "ALL ASSERTIONS PASSED"
exit 0
