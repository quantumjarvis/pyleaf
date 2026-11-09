#!/usr/bin/env bash
# E2E contract for pyleaf M3 (WI 3.9, PYL-40).
#
# Fresh venv -> editable-install core/boot/tx/starter/data/web/actuator/
# security + pyjwt -> boot the bank example on a free port under real
# uvicorn -> HTTP assertions:
#   1. happy path: POST /transfer with a TELLER JWT -> 200, balances
#      moved, one audit row, the AFTER_COMMIT notification "sent"
#      exactly once (the stub mailer is probed via GET /probe/outbox)
#   2. failure path: a transfer that overdraws -> 422 from the
#      @ControllerAdvice; balances UNCHANGED; the REQUIRES_NEW audit row
#      for the attempt EXISTS; NO new notification (AFTER_COMMIT never
#      fired on rollback)
#   3. security: POST /transfer without a token -> 401, with a VIEWER
#      (wrong-role) JWT -> 403, /actuator/health anonymous -> 401
#      (actuator is protected by the built-in ACTUATOR-role rule)
#
# JWTs are minted in-process with pyjwt (installed into the venv below):
# HS256 with the configured pyleaf.security.jwt.secret, roles claim
# ["TELLER"] / ["VIEWER"], exp required by JwtAuthenticationProvider.
#
# The server runs as a background process on a free 127.0.0.1 port with
# a throwaway sqlite file per run; a trap kills it and removes the temp
# dir on exit. Exit 0 only when all assertions pass.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.e2e-venv"
PYTHON="$VENV/bin/python"
SERVER_PID=""
TMPDIR_E2E=""

# Must match pyleaf.security.jwt.secret in examples/bank/application.yaml.
JWT_KEY="bank-e2e-hs256-key-not-for-production"

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; exit 1; }

cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [ -n "$TMPDIR_E2E" ]; then
        rm -rf "$TMPDIR_E2E"
    fi
}
trap cleanup EXIT

echo "== creating fresh venv at $VENV =="
rm -rf "$VENV"
uv venv "$VENV" --quiet
uv pip install --python "$PYTHON" --quiet \
    -e packages/pyleaf-core \
    -e packages/pyleaf-boot \
    -e packages/pyleaf-tx \
    -e packages/pyleaf-starter-sqlalchemy \
    -e packages/pyleaf-data \
    -e packages/pyleaf-web \
    -e packages/pyleaf-actuator \
    -e packages/pyleaf-security \
    pyjwt

# --- Boot: `python -m bank` on a free port, file db in a temp dir -----
echo "== booting bank via uvicorn =="
PORT="$("$PYTHON" - <<'PYEOF'
import socket

s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PYEOF
)"
TMPDIR_E2E="$(mktemp -d)"
LOG="$TMPDIR_E2E/server.log"

(cd examples/bank && "$PYTHON" -m bank \
    --pyleaf.web.port="$PORT" \
    --app.datasource.url="sqlite:///$TMPDIR_E2E/bank.db") >"$LOG" 2>&1 &
SERVER_PID=$!

BASE="http://127.0.0.1:$PORT"
READY=""
for _ in $(seq 1 60); do
    if curl -sf "$BASE/health" >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 0.5
done
if [ -z "$READY" ]; then
    cat "$LOG" >&2
    fail "bank did not become ready on port $PORT"
fi
echo "bank serving on $BASE (pid $SERVER_PID)"
pass "boot: real uvicorn serves the auto-configured FastAPI app"

# --- Mint the test JWTs (HS256, roles claim -> ROLE_* authorities) ----
TELLER_TOKEN="$("$PYTHON" - "$JWT_KEY" TELLER <<'PYEOF'
import sys
import time

import jwt

now = int(time.time())
claims = {"sub": "teller-1", "iat": now, "exp": now + 3600, "roles": [sys.argv[2]]}
print(jwt.encode(claims, sys.argv[1], algorithm="HS256"))
PYEOF
)"
VIEWER_TOKEN="$("$PYTHON" - "$JWT_KEY" VIEWER <<'PYEOF'
import sys
import time

import jwt

now = int(time.time())
claims = {"sub": "viewer-1", "iat": now, "exp": now + 3600, "roles": [sys.argv[2]]}
print(jwt.encode(claims, sys.argv[1], algorithm="HS256"))
PYEOF
)"

# --- Assertions over plain HTTP ----------------------------------------
"$PYTHON" - "$BASE" "$TELLER_TOKEN" "$VIEWER_TOKEN" <<'PYEOF'
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1]
TELLER = sys.argv[2]
VIEWER = sys.argv[3]


def request(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None


def check(cond, label):
    if not cond:
        print(f"FAIL: {label}", file=sys.stderr)
        sys.exit(1)
    print(f"PASS: {label}")


def balances():
    status, body = request("GET", "/accounts", token=TELLER)
    assert status == 200, f"GET /accounts -> {status}"
    return {a["id"]: a["balance_cents"] for a in body}


# -- Security gates first: nothing reaches the app without a credential.
status, _ = request("POST", "/transfer", {
    "from_account_id": 1, "to_account_id": 2, "amount_cents": 100,
})
check(status == 401, "1a: POST /transfer without token -> 401")

status, body = request("GET", "/actuator/health")
check(status == 401, "1b: /actuator/health anonymous -> 401 (ACTUATOR role)")

status, _ = request("POST", "/transfer", {
    "from_account_id": 1, "to_account_id": 2, "amount_cents": 100,
}, token=VIEWER)
check(status == 403, "1c: POST /transfer with VIEWER role -> 403")

# -- Setup: two accounts via the authenticated CRUD surface.
status, alice = request("POST", "/accounts", {
    "owner": "alice", "balance_cents": 10000,
}, token=TELLER)
check(
    status == 201 and isinstance(alice.get("id"), int),
    "2a: POST /accounts (alice, 10000) -> 201",
)
status, bob = request("POST", "/accounts", {
    "owner": "bob", "balance_cents": 5000,
}, token=TELLER)
check(
    status == 201 and isinstance(bob.get("id"), int),
    "2b: POST /accounts (bob, 5000) -> 201",
)
A, B = alice["id"], bob["id"]

# -- 3. Happy path: commit moves balances, audits once, notifies once.
status, receipt = request("POST", "/transfer", {
    "from_account_id": A, "to_account_id": B, "amount_cents": 2500,
}, token=TELLER)
check(
    status == 200
    and receipt.get("from_balance_cents") == 7500
    and receipt.get("to_balance_cents") == 7500,
    "3a: POST /transfer 2500 -> 200 with post-transfer balances",
)
check(
    balances() == {A: 7500, B: 7500},
    "3b: GET /accounts shows the committed transfer",
)
status, audits = request("GET", "/audits", token=TELLER)
check(
    status == 200
    and len(audits) == 1
    and audits[0]["from_account_id"] == A
    and audits[0]["to_account_id"] == B
    and audits[0]["amount_cents"] == 2500,
    "3c: GET /audits lists the single attempt row",
)
status, outbox = request("GET", "/probe/outbox", token=TELLER)
check(
    status == 200
    and len(outbox) == 1
    and outbox[0]["to"] == f"account-{B}@bank.local",
    "3d: AFTER_COMMIT notification sent exactly once",
)

# -- 4. Failure path: mid-way failure rolls back, audit stays, no mail.
status, body = request("POST", "/transfer", {
    "from_account_id": A, "to_account_id": B, "amount_cents": 999999,
}, token=TELLER)
check(
    status == 422 and body.get("error") == "insufficient_funds",
    "4a: overdrawn POST /transfer -> 422 via @ControllerAdvice",
)
check(
    balances() == {A: 7500, B: 7500},
    "4b: balances UNCHANGED after the rolled-back transfer",
)
status, audits = request("GET", "/audits", token=TELLER)
check(
    status == 200
    and len(audits) == 2
    and audits[1]["amount_cents"] == 999999,
    "4c: REQUIRES_NEW audit row for the failed attempt persisted",
)
status, outbox = request("GET", "/probe/outbox", token=TELLER)
check(
    status == 200 and len(outbox) == 1,
    "4d: NO notification sent — AFTER_COMMIT never fired on rollback",
)
PYEOF

# --- Shutdown: SIGTERM -> uvicorn -> lifespan closes the context ----------
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""
echo "== server stopped; log tail =="
tail -5 "$LOG" || true

echo "ALL ASSERTIONS PASSED"
exit 0
