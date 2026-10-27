"""``PyleafApplication.run``: env, scan, auto-config, refresh, runners, shutdown.

The single entry point wires the whole boot sequence:

1. ``load_environment`` + logging setup (driven by ``pyleaf.logging.*``);
2. banner print (suppressible via ``banner=False`` or ``pyleaf.banner=false``);
3. ``ApplicationContext`` + package scan;
4. entry-point auto-configuration discovery/registration;
5. ``ctx.refresh(eager=True)``;
6. ``ApplicationRunner`` / ``CommandLineRunner`` beans invoked in ``@Order``;
7. SIGINT/SIGTERM handlers closing the context within
   ``pyleaf.shutdown.timeout`` seconds.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import logging
import signal
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType
from typing import Any, Protocol, runtime_checkable

from pyleaf.core import ApplicationContext, Environment

from .autoconfigure import discover_autoconfigs, register_autoconfigs
from .config import load_environment

_logger = logging.getLogger(__name__)

BANNER_PROPERTY = "pyleaf.banner"
LOG_LEVEL_PROPERTY = "pyleaf.logging.level"
LOG_FORMAT_PROPERTY = "pyleaf.logging.format"
SHUTDOWN_TIMEOUT_PROPERTY = "pyleaf.shutdown.timeout"

DEFAULT_LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"
DEFAULT_SHUTDOWN_TIMEOUT = 5.0


@runtime_checkable
class ApplicationRunner(Protocol):
    """Startup callback protocol: ``run`` receives the raw args tuple."""

    def run(self, args: tuple[str, ...]) -> None:
        """Run once after refresh; ``args`` is the raw CLI args tuple."""
        ...


@runtime_checkable
class CommandLineRunner(Protocol):
    """Startup callback protocol: ``run`` receives the CLI args spread."""

    def run(self, *args: str) -> None:
        """Run once after refresh with each CLI arg as a parameter."""
        ...


def _setup_logging(env: Environment) -> None:
    """Configure root logging from ``pyleaf.logging.*`` — never clobbers."""
    root = logging.getLogger()
    if root.handlers:
        return
    level_value = env.get(LOG_LEVEL_PROPERTY, default="INFO")
    if isinstance(level_value, str):
        level = logging.getLevelNamesMapping().get(
            level_value.upper(), logging.INFO
        )
    else:
        level = int(level_value)
    fmt = str(env.get(LOG_FORMAT_PROPERTY, default=DEFAULT_LOG_FORMAT))
    logging.basicConfig(level=level, format=fmt)


def shutdown(ctx: ApplicationContext, timeout: float) -> None:
    """Close ``ctx`` on a helper thread; warn and return if it overruns.

    The timeout lives here, not in ``close()``: a stuck destroy hook can
    never hang the shutdown path.
    """
    thread = threading.Thread(
        target=ctx.close, name="pyleaf-shutdown", daemon=True
    )
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        _logger.warning(
            "Application context shutdown timed out after %.1fs", timeout
        )


def install_signal_handlers(
    ctx: ApplicationContext, timeout: float
) -> Callable[[int, FrameType | None], None]:
    """Install SIGINT/SIGTERM handlers that close ``ctx`` within ``timeout``.

    ``signal.signal`` only works on the main thread; anywhere else it
    raises ``ValueError`` and the install degrades to a logged no-op.
    Returns the handler so callers/tests can invoke it directly.
    """

    def handler(signum: int, frame: FrameType | None) -> None:
        _logger.info(
            "Received signal %s; closing application context", signum
        )
        shutdown(ctx, timeout)

    try:
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)
    except ValueError:
        _logger.debug(
            "Signal handlers not installed (not running on the main thread)"
        )
    return handler


def _version() -> str:
    try:
        return importlib.metadata.version("pyleaf-boot")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _wants_spread_args(run: Callable[..., Any]) -> bool:
    """True when ``run`` is variadic (``run(*args)`` CommandLineRunner style)."""
    try:
        params = inspect.signature(run).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params)


def _invoke_runners(ctx: ApplicationContext, args: tuple[str, ...]) -> None:
    """Call every runner bean once, ordered by ``@Order`` then bean name.

    Both protocols reduce to "has a ``run`` method" structurally, so the
    call convention is picked from the signature: variadic ``run`` gets
    the args spread (CommandLineRunner), positional ``run`` gets the raw
    tuple (ApplicationRunner). Runner exceptions propagate to the caller.
    """
    for bean in ctx.get_all(object):
        if not isinstance(bean, (ApplicationRunner, CommandLineRunner)):
            continue
        run: Callable[..., Any] = bean.run
        if _wants_spread_args(run):
            run(*args)
        else:
            run(args)


class PyleafApplication:
    """Boot entry point for a pyleaf application."""

    @classmethod
    def run(
        cls,
        source: str,
        args: Sequence[str] = (),
        *,
        profiles: Sequence[str] = (),
        exclude: Sequence[str] = (),
        config_dir: str | Path = ".",
        banner: bool = True,
    ) -> ApplicationContext:
        """Boot the application and return the live application context.

        ``source`` is the package scanned for components; ``args`` are the
        raw CLI arguments (also parsed for ``--key=value`` properties).
        Auto-configs discovered through the ``pyleaf.autoconfigure`` entry
        points register after scanning, so user beans satisfy
        ``@ConditionalOnMissingBean``. A failing runner aborts startup:
        the context is closed and the error propagates.
        """
        started = time.monotonic()
        cli_args = tuple(args)

        env = load_environment(config_dir, profiles, cli_args=cli_args)
        _setup_logging(env)

        if banner and env.get(BANNER_PROPERTY, bool, True):
            print(f"pyleaf {_version()} — {source}")

        ctx = ApplicationContext(scan=[source], environment=env)
        register_autoconfigs(ctx, discover_autoconfigs(), exclude)
        ctx.refresh(eager=True)
        _logger.info(
            "Started %s in %.3f seconds", source, time.monotonic() - started
        )

        try:
            _invoke_runners(ctx, cli_args)
        except BaseException:
            ctx.close()
            raise

        timeout = float(
            env.get(SHUTDOWN_TIMEOUT_PROPERTY, float, DEFAULT_SHUTDOWN_TIMEOUT)
        )
        install_signal_handlers(ctx, timeout)
        return ctx
