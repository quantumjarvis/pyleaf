# pyleaf benchmarks

M1 performance benchmark suite (Jira PYL-18). Benchmarks live under
`benchmarks/m1/` and are deliberately **excluded** from the default pytest
`testpaths` (`packages/*/tests`) — they only run when explicitly targeted.

## Run

```bash
uv run pytest benchmarks/m1 -q          # run benchmarks + threshold asserts
uv run pytest benchmarks/m1 --benchmark-only -q
```

Every benchmark asserts its M1 threshold, so a regression fails the suite
even without a baseline comparison.

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

## Baselines and compare

`benchmarks/baselines/local.json` is the committed local baseline, produced
with:

```bash
uv run pytest benchmarks/m1 --benchmark-save=local
cp .benchmarks/*/0001_local.json benchmarks/baselines/local.json
```

Compare a fresh run against the committed baseline:

```bash
uv run pytest benchmarks/m1 --benchmark-compare=benchmarks/baselines/local.json
# fail the run on regressions beyond a tolerance:
uv run pytest benchmarks/m1 \
  --benchmark-compare=benchmarks/baselines/local.json \
  --benchmark-compare-fail=mean:50%
```

Note: `test_aop_async_chain_of_3` reports the per-batch median (200 awaited
calls per measured batch); the asserted per-call threshold divides it by the
batch size. `test_bench_import.py` reports subprocess wall time in the stats
table; the asserted value is the inner `import pyleaf.core` time measured
inside the child interpreter.
