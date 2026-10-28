# Verification Commands

To verify the workspace, run:

```bash
cd /Users/anandy/git/python-fw/pyleaf
uv sync                                          # resolves and installs workspace
uv run python -c "import pyleaf.core, pyleaf.boot; print('imports ok')"
uv run pytest                                    # reports >= 2 passed
uv run mypy --strict packages/pyleaf-core/src packages/pyleaf-boot/src
uv run ruff check .
find packages -path '*/src/pyleaf/__init__.py'   # must print NOTHING
```

## Extended checks (M1 complete)

```bash
uv run pytest -q                                  # full suite, all packages
uv run mypy --strict packages/*/src               # all five packages
uv run pytest benchmarks/m1 -q                    # perf suite; excluded from
                                                  # default testpaths, each
                                                  # bench asserts a threshold
uv run pytest benchmarks/m1 \
  --benchmark-compare=benchmarks/baselines/local.json \
  --benchmark-compare-fail=mean:50%               # regression check vs baseline

bash scripts/e2e_m1.sh                            # fresh-venv E2E contract:
                                                  # starter creates Engine,
                                                  # user bean -> backoff,
                                                  # sqlalchemy absent -> OnClass
                                                  # reason, self-invocation == 2
bash scripts/quality_m1.sh                        # pip-audit, bandit, ruff,
                                                  # coverage gates (resolver /
                                                  # aop / conditions /
                                                  # autoconfigure >= 90%,
                                                  # TOTAL >= 80%)
bash scripts/quality_m1.sh --mutmut               # also run mutation testing
                                                  # (minutes; CI-optional)

uv run mkdocs build --strict                      # docs/ must build clean
uv run mkdocs serve                               # local docs preview

cd examples && uv run python -m hello             # quickstart smoke test:
                                                  # prints the greeting,
                                                  # ASPECT_INTERNAL_COUNT=2,
                                                  # ENGINE_OK url=...
```
