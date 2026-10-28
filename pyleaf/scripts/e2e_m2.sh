#!/usr/bin/env bash
# E2E contract for pyleaf M2 (WI 2.7, PYL-28).
#
# Fresh venv -> editable-install core/boot/starter/web/actuator -> boot the
# todo-api example on a free port under real uvicorn -> six HTTP assertions:
#   1. /actuator/health -> 200 UP
#   2. CRUD round-trip: POST -> 201 -> GET list -> PUT -> DELETE -> GET ->
#      404 rendered by the @ControllerAdvice
#   3. /actuator/beans lists todoService and the request-scoped requestAudit
#      WITHOUT instantiating them (RequestAudit.__init__ raises
#      ScopeNotActive outside a request scope, so a 200 proves the endpoint
#      enumerates definitions only)
#   4. /actuator/env masks the configured app.api-secret-key as ******
#   5. the @WebMiddleware stamps an x-request-id header on responses
#   6. /openapi.json contains the todo paths
#
# The server runs as a background process on a free 127.0.0.1 port with a
# throwaway sqlite file; a trap kills it and removes the temp dir on exit.
# Exit 0 only when all assertions pass.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.e2e-venv"
PYTHON="$VENV/bin/python"
SERVER_PID=""
TMPDIR_E2E=""

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
    -e packages/pyleaf-starter-sqlalchemy \
    -e packages/pyleaf-web \
    -e packages/pyleaf-actuator

# --- Boot: `python -m todo_api` on a free port, file db in a temp dir -----
echo "== booting todo-api via uvicorn =="
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

(cd examples/todo-api && "$PYTHON" -m todo_api \
    --pyleaf.web.port="$PORT" \
    --app.datasource.url="sqlite:///$TMPDIR_E2E/todo.db") >"$LOG" 2>&1 &
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
    fail "todo-api did not become ready on port $PORT"
fi
echo "todo-api serving on $BASE (pid $SERVER_PID)"
pass "boot: real uvicorn serves the auto-configured FastAPI app"

# --- Assertions 1-6 over plain HTTP ----------------------------------------
"$PYTHON" - "$BASE" <<'PYEOF'
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1]
SECRET = "dev-only-secret-value"


def request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, resp.headers, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, exc.headers, json.loads(raw) if raw else None


def check(cond, label):
    if not cond:
        print(f"FAIL: {label}", file=sys.stderr)
        sys.exit(1)
    print(f"PASS: {label}")


# 1. /actuator/health -> 200 UP
status, _, body = request("GET", "/actuator/health")
check(
    status == 200 and body.get("status") == "UP",
    "1: /actuator/health -> 200 UP",
)

# 2. CRUD round-trip
status, _, body = request("POST", "/todos", {"title": "write docs"})
check(
    status == 201
    and body.get("title") == "write docs"
    and body.get("completed") is False
    and isinstance(body.get("id"), int),
    "2a: POST /todos -> 201 created",
)
todo_id = body["id"]

status, _, body = request("GET", "/todos")
check(
    status == 200 and any(t.get("id") == todo_id for t in body),
    "2b: GET /todos lists the created todo",
)

status, _, body = request("GET", f"/todos/{todo_id}")
check(
    status == 200 and body.get("id") == todo_id,
    "2c: GET /todos/{id} -> 200",
)

status, _, body = request("PUT", f"/todos/{todo_id}", {"completed": True})
check(
    status == 200 and body.get("completed") is True,
    "2d: PUT /todos/{id} applies the update",
)

status, _, _ = request("DELETE", f"/todos/{todo_id}")
check(status == 204, "2e: DELETE /todos/{id} -> 204")

status, _, body = request("GET", f"/todos/{todo_id}")
check(
    status == 404
    and body.get("error") == "todo_not_found"
    and "not found" in str(body.get("detail")),
    "2f: GET after DELETE -> 404 rendered by @ControllerAdvice",
)

# 3. /actuator/beans: definitions only — lazy/request beans stay untouched.
status, _, body = request("GET", "/actuator/beans")
names = {b["name"]: b for b in body} if isinstance(body, list) else {}
check(
    status == 200
    and "todoService" in names
    and names["todoService"]["type"].endswith("TodoService")
    and names["todoService"]["scope"] == "singleton",
    "3a: /actuator/beans lists the todoService definition",
)
check(
    names.get("requestAudit", {}).get("scope") == "request"
    and names["requestAudit"]["lazy"] is True,
    "3b: request-scoped requestAudit listed without instantiation",
)

# 4. /actuator/env masks the configured secret key.
status, _, body = request("GET", "/actuator/env")
props = {}
for source in body if isinstance(body, list) else []:
    props.update(source.get("properties", {}))
check(
    status == 200
    and props.get("app.api-secret-key") == "******"
    and SECRET not in json.dumps(body),
    "4: /actuator/env masks app.api-secret-key",
)

# 5. @WebMiddleware stamps the request-id header.
status, headers, _ = request("GET", "/todos")
check(
    status == 200 and bool(headers.get("x-request-id")),
    "5: response carries the @WebMiddleware x-request-id header",
)

# 6. OpenAPI contains the todo paths.
status, _, body = request("GET", "/openapi.json")
paths = body.get("paths", {}) if isinstance(body, dict) else {}
check(
    status == 200 and "/todos" in paths and "/todos/{todo_id}" in paths,
    "6: /openapi.json contains /todos and /todos/{todo_id}",
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
