# pyleaf benchmarks

Performance benchmark suites for M1 (Jira PYL-18), M2 (Jira PYL-29) and
M3 (Jira PYL-32). Benchmarks live under `benchmarks/m1/`,
`benchmarks/m2/` and `benchmarks/m3/` and are deliberately **excluded**
from the default pytest `testpaths` (`packages/*/tests`) — they only run
when explicitly targeted.

## Run

```bash
uv run pytest benchmarks/m1 -q          # m1 benchmarks + threshold asserts
uv run pytest benchmarks/m2 -q          # m2 benchmarks + threshold asserts
uv run pytest benchmarks/m3 -q          # m3 benchmarks + threshold asserts
uv run pytest benchmarks/m3 --benchmark-only -q
```

Every benchmark asserts its threshold, so a regression fails the suite
even without a baseline comparison.

M1 (container core):

| Benchmark | Threshold |
|---|---|
| `test_bench_refresh.py` lazy refresh, 1,000 defs | < 300 ms |
| `test_bench_refresh.py` eager refresh, 1,000 defs | < 1 s |
| `test_bench_get.py` resolved `ctx.get()` | < 2 us/call |
| `test_bench_aop.py` sync chain of 3 `@Around` | < 5 us/call |
| `test_bench_aop.py` async chain of 3 `@Around` | < 15 us/call |
| `test_bench_aop.py` unmatched bean vs direct call | < 2x direct or < 0.5 us |
| `test_bench_conditions.py` 500 conditional defs | < 100 ms |
| `test_bench_scan.py` fake 500-module package scan | < 500 ms |
| `test_bench_memory.py` 1,000 singletons (tracemalloc peak) | < 5 MiB |
| `test_bench_import.py` cold `import pyleaf.core` (subprocess) | < 50 ms |

M2 (web layer):

| Benchmark | Threshold |
|---|---|
| `test_bench_controller_overhead.py` `@RestController` vs raw FastAPI | p50 overhead < 10% (measured ratio) |
| `test_bench_request_scope.py` scope cycle (activate + create + teardown) | < 50 us |
| `test_bench_advice_dispatch.py` advice vs FastAPI exception handler | p50 overhead < 25% (measured ratio) |
| `test_bench_actuator_beans.py` `/actuator/beans` on 200 defs | < 50 ms |
| `test_bench_middleware_hop.py` `@WebMiddleware` added latency per hop | < 20 us vs equivalent raw stack |

The middleware gate is a delta against a hand-mounted
`BaseHTTPMiddleware` stack: Starlette's own plumbing (~130 us/hop) is
machinery pyleaf does not control, so what is gated is the latency the
bean layer adds per hop.

M3 (transaction infrastructure + data/security hot paths):

| Benchmark | Threshold |
|---|---|
| `test_bench_tx_resource_lookup.py` `TSM.get_resource` on an active context | < 1 us |
| `test_bench_transactional_noop.py` `@Transactional` REQUIRED join, sync | < 20 us |
| `test_bench_transactional_noop.py` async join (200-call batch) | < 40 us/call |
| `test_bench_repository.py` `repo.find_by_id` vs raw `session.get`, ambient tx | same-run ratio < 1.10x and < 500 us |
| `test_bench_jwt.py` HS256 `JwtAuthenticationProvider.authenticate` | < 100 us |
| `test_bench_preauthorize.py` `@PreAuthorize` call vs pass-through advised call | same-run ratio < 15x and < 25 us |

The repository gate is the CI-visible version of the 1.05x same-run pin
in `packages/pyleaf-data/tests/test_crud_repository.py`: both arms
`expire_all()` + get the same pk, so the only delta is session
resolution + delegation. The preauthorize gate compares against a
method advised by a no-op `@Around` aspect (same wrapper/Invocation
dispatch, no check), so the ratio isolates the check itself — a
completely unadvised ~0.1 us method would make the ratio measure the
AOP dispatch already gated in m1.

## M2 memory soak

`benchmarks/m2/soak_request_scope.py` runs 100,000 sequential
`TestClient` requests through `RequestScopeMiddleware`, sampling RSS
every 5,000; growth must stay under 20 MB. It is a standalone script
(not collected by pytest) and runs via `scripts/quality_m2.sh`:

```bash
uv run python benchmarks/m2/soak_request_scope.py
```

## Baselines and compare

`benchmarks/baselines/local.json` is the committed local baseline
(m1 + m2 entries), produced with:

```bash
uv run pytest benchmarks/m1 --benchmark-save=local
cp .benchmarks/*/0001_local.json benchmarks/baselines/local.json
# m2 rows were merged in afterwards from a --benchmark-save run
```

Compare a fresh run against the committed baseline:

```bash
uv run pytest benchmarks/m1 --benchmark-compare=benchmarks/baselines/local.json
# fail the run on regressions beyond a tolerance:
uv run pytest benchmarks/m1 \
  --benchmark-compare=benchmarks/baselines/local.json \
  --benchmark-compare-fail=mean:50%
```

`benchmarks/baselines/github.json` is the CI-runner reference: it is
seeded deliberately from saved CI artifacts (`.benchmarks/` uploaded by
the benchmarks job) — never rebaselined in a normal commit. In CI the
cross-run compare against it is **informational only** (see the comment
in `.github/workflows/ci.yml`): ubuntu-latest's heterogeneous CPU pool
produced false regressions on identical code, so the real gates are the
absolute threshold assertions and the same-run ratio benchmarks.

Note: `test_aop_async_chain_of_3` reports the per-batch median (200 awaited
calls per measured batch); the asserted per-call threshold divides it by the
batch size. `test_bench_import.py` reports subprocess wall time in the stats
table; the asserted value is the inner `import pyleaf.core` time measured
inside the child interpreter.
