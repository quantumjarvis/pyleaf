"""``python -m todo_api``: boot the app and serve it via uvicorn.

Run from ``examples/todo-api/`` (or anywhere with that directory on
``PYTHONPATH``). A ``FastAPI`` bean exists (WebAutoConfiguration), so
``run`` serves it on ``pyleaf.web.host``/``pyleaf.web.port`` and blocks;
``--key=value`` CLI args override ``application.yaml`` (e.g.
``--pyleaf.web.port=9000 --app.datasource.url=sqlite:////tmp/todo.db``),
and ``--no-server`` boots without serving.
"""

import sys
from pathlib import Path

from pyleaf.boot import PyleafApplication

_CONFIG_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    """Boot and serve; the app lifespan closes the context on shutdown."""
    PyleafApplication.run(
        "todo_api",
        args=sys.argv[1:],
        config_dir=_CONFIG_DIR,
    )


if __name__ == "__main__":
    main()
