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