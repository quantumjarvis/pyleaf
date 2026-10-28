"""``PyleafApplication.run``: env, scan, auto-config, refresh, runners, shutdown.

The single entry point wires the whole boot sequence:

1. ``load_environment`` + logging setup (driven by ``pyleaf.logging.*``);
2. banner print (suppressible via ``banner=False`` or ``pyleaf.banner=false``);
3. ``ApplicationContext`` + package scan;
4. entry-point auto-configuration discovery/registration;
5. ``ctx.refresh(eager=True)``;
6. ``ApplicationRunner`` / ``CommandLineRunner`` beans invoked in ``@Order``;
7. server mode when a ``FastAPI`` bean exists and web is enabled: uvicorn
   serves the app on ``pyleaf.web.host``/``pyleaf.web.port`` and blocks until
   shutdown — uvicorn owns the signals and the app lifespan closes the
   context. ``no_server=True``, the ``--no-server`` CLI flag or
   ``pyleaf.web.enabled=false`` suppress serving;
8. otherwise SIGINT/SIGTERM handlers closing the context within
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

from pyleaf.core import (
    ApplicationContext,
    Environment,
    NoSuchBean,
    NoUniqueBean,
)

from .autoconfigure import discover_autoconfigs, register_autoconfigs
from .config import load_environment

_logger = logging.getLogger(__name__)

BANNER_PROPERTY = "pyleaf.banner"
LOG_LEVEL_PROPERTY = "pyleaf.logging.level"
LOG_FORMAT_PROPERTY = "pyleaf.logging.format"
SHUTDOWN_TIMEOUT_PROPERTY = "pyleaf.shutdown.timeout"
WEB_ENABLED_PROPERTY = "pyleaf.web.enabled"
WEB_HOST_PROPERTY = "pyleaf.web.host"
WEB_PORT_PROPERTY = "pyleaf.web.port"
WEB_WORKERS_PROPERTY = "pyleaf.web.workers"
WEB_ACCESS_LOG_PROPERTY = "pyleaf.web.access-log"
NO_SERVER_ARG = "--no-server"

DEFAULT_LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"
DEFAULT_SHUTDOWN_TIMEOUT = 5.0
# Deliberately not 0.0.0.0 (Spring's default): binding only loopback keeps
# a first `run()` from exposing the service on every interface.
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8080
DEFAULT_WEB_WORKERS = 1
DEFAULT_WEB_ACCESS_LOG = True


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


def _fastapi_app(ctx: ApplicationContext) -> Any | None:
    """Return the registered FastAPI app bean, or None when unservable.

    fastapi is optional (it arrives with pyleaf-web); a missing package or
    missing bean means "nothing to serve". A non-unique bean is ambiguous —
    logged and treated as unservable rather than picking one arbitrarily.
    """
    try:
        from fastapi import FastAPI
    except ImportError:
        return None
    try:
        return ctx.get(FastAPI)
    except NoUniqueBean:
        _logger.warning(
            "Multiple FastAPI beans are registered; not serving any —"
            " mark one @Primary or serve it manually"
        )
        return None
    except NoSuchBean:
        return None


def _serve(ctx: ApplicationContext, env: Environment, app: Any) -> bool:
    """Serve ``app`` through uvicorn, blocking until the server shuts down.

    Returns False without serving when uvicorn is not importable — the
    caller then falls back to the plain signal-handler path. Uvicorn owns
    signal handling in server mode and drives the app lifespan; the
    WebAutoConfiguration lifespan hook closes ``ctx`` on shutdown.
    """
    try:
        import uvicorn
    except ImportError:
        _logger.warning(
            "uvicorn is not installed; the FastAPI app will not be served."
            " Install uvicorn, or disable serving via %s=false or %s",
            WEB_ENABLED_PROPERTY,
            NO_SERVER_ARG,
        )
        return False
    config = uvicorn.Config(
        app,
        host=str(env.get(WEB_HOST_PROPERTY, str, DEFAULT_WEB_HOST)),
        port=int(env.get(WEB_PORT_PROPERTY, int, DEFAULT_WEB_PORT)),
        workers=int(env.get(WEB_WORKERS_PROPERTY, int, DEFAULT_WEB_WORKERS)),
        access_log=bool(
            env.get(WEB_ACCESS_LOG_PROPERTY, bool, DEFAULT_WEB_ACCESS_LOG)
        ),
    )
    server = uvicorn.Server(config)
    # Stash the server on the app: a run() hosted in a worker thread cannot
    # rely on uvicorn's signal handlers (main thread only), so callers stop
    # it programmatically via ``should_exit``/``force_exit``.
    app.state.uvicorn_server = server
    try:
        server.run()
    except BaseException:
        # A startup crash may have skipped the lifespan shutdown that
        # closes the context; close it here so nothing leaks.
        ctx.close()
        raise
    return True


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
        no_server: bool = False,
    ) -> ApplicationContext:
        """Boot the application and return the application context.

        ``source`` is the package scanned for components; ``args`` are the
        raw CLI arguments (also parsed for ``--key=value`` properties).
        Auto-configs discovered through the ``pyleaf.autoconfigure`` entry
        points register after scanning, so user beans satisfy
        ``@ConditionalOnMissingBean``. A failing runner aborts startup:
        the context is closed and the error propagates.

        When a ``FastAPI`` bean exists and ``pyleaf.web.enabled`` is not
        false, ``run`` serves it through uvicorn and blocks until shutdown
        — uvicorn owns the signals and the app lifespan closes the context,
        so the returned context is already closed. ``no_server=True`` or a
        ``--no-server`` CLI arg keeps the CLI behavior: ``run`` installs
        SIGINT/SIGTERM handlers and returns the live context immediately.
        """
        started = time.monotonic()
        cli_args = tuple(args)
        # Consume ``--no-server`` before env loading: _parse_cli rejects
        # bare ``--flags`` and runners receive the remaining args.
        no_server_arg = NO_SERVER_ARG in cli_args
        if no_server_arg:
            cli_args = tuple(a for a in cli_args if a != NO_SERVER_ARG)

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
        serve = (
            not no_server
            and not no_server_arg
            and bool(env.get(WEB_ENABLED_PROPERTY, bool, True))
        )
        if serve:
            app = _fastapi_app(ctx)
            if app is not None and _serve(ctx, env, app):
                # Serving owned the shutdown path: the app lifespan already
                # closed the context; no boot signal handlers are installed.
                return ctx
        install_signal_handlers(ctx, timeout)
        return ctx
