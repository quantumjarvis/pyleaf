# pyleaf

A Spring-style DI/auto-configuration framework for Python.

## Project Structure

```
pyleaf/
├── pyproject.toml         # workspace root
├── README.md              # this file
├── AGENTS.md              # verification commands
├── .gitignore             # standard Python ignores
└── packages/
    ├── pyleaf-core/
    │   ├── pyproject.toml
    │   ├── src/pyleaf/core/__init__.py
    │   ├── src/pyleaf/core/py.typed
    │   └── tests/test_smoke.py
    └── pyleaf-boot/
        ├── pyproject.toml
        ├── src/pyleaf/boot/__init__.py
        ├── src/pyleaf/boot/py.typed
        └── tests/test_smoke.py
```

## Verification

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