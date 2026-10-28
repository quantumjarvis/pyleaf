"""``python -m hello``: boot the app and print the contract markers.

Run from ``examples/`` (or anywhere with ``examples`` on ``PYTHONPATH``).
The module is also imported as ``hello.__main__`` during package scanning,
so all work stays behind the ``__main__`` guard.
"""

from pathlib import Path

from pyleaf.boot import PyleafApplication
from pyleaf.core import NoSuchBean

from .aspects import InvocationCounterAspect
from .greeting import GreetingService

_CONFIG_DIR = Path(__file__).resolve().parent


def main() -> None:
    """Boot, greet, exercise the aspect and probe for the Engine bean."""
    ctx = PyleafApplication.run("hello", config_dir=_CONFIG_DIR, no_server=True)
    try:
        service = ctx.get(GreetingService)
        print(service.greet())
        # public() -> self.internal() plus a direct internal() call must
        # intercept internal twice (self-invocation interception proof).
        service.public()
        service.internal()
        aspect = ctx.get(InvocationCounterAspect)
        print(f"ASPECT_INTERNAL_COUNT={aspect.counts.get('internal', 0)}")
        try:
            engine = ctx.get_bean("engine")
        except NoSuchBean:
            print("ENGINE_MISSING")
        else:
            print(f"ENGINE_OK url={engine.url}")
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
