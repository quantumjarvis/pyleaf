"""``PyleafApplication.run`` server mode: uvicorn serving and shutdown.

The server tests run ``run()`` in a worker thread: uvicorn only installs
its signal handlers on the main thread, so a thread keeps pytest's own
handlers untouched and the server is stopped via ``should_exit``. The
server handle is reachable as ``app.state.uvicorn_server``.
"""

import importlib
import signal
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pyleaf.boot.application as application_mod
import pytest
from fastapi import FastAPI
from pyleaf.boot import PyleafApplication
from pyleaf.core import ApplicationContext, NoSuchBean

_WEBAPP = """\
from pyleaf.core import ApplicationContext, Component, PreDestroy
from pyleaf.web import GetMapping, RestController

closed = []
captured = {}


@Component
class Probe:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.ctx = ctx
        captured["ctx"] = ctx

    @PreDestroy
    def bye(self) -> None:
        closed.append("x")


@RestController("/hello")
class HelloController:
    @GetMapping("")
    def hello(self) -> dict:
        return {"msg": "hi"}
"""


@pytest.fixture
def webapp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A scannable ``webapp`` package on sys.path; yields the config dir."""
    pkg = tmp_path / "webapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(_WEBAPP, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("webapp", None)
    importlib.invalidate_caches()
    yield tmp_path
    sys.modules.pop("webapp", None)


@pytest.fixture
def restore_signals() -> Iterator[None]:
    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGTERM, prev_term)
    signal.signal(signal.SIGINT, prev_int)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _error_summaries(errors: list[BaseException] | None) -> list[str]:
    return [f"{e.__class__.__name__}: {e}" for e in (errors or [])]


def _wait_listening(
    port: int,
    thread: threading.Thread | None = None,
    errors: list[BaseException] | None = None,
    timeout: float = 60.0,
) -> None:
    """Poll until ``port`` accepts a connection, or the serving thread dies.

    ``thread``/``errors`` describe the ``run()`` worker: once it has exited
    the port can never bind, so fail fast with the captured exception
    instead of burning the whole budget. A timeout also reports the thread
    state and any captured error so a CI flake exposes the root cause
    rather than a bare "nothing is listening".
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _listening(port):
            return
        if thread is not None and not thread.is_alive():
            msg = (
                f"serving thread exited before 127.0.0.1:{port} was listening "
                f"(alive={thread.is_alive()}, errors={_error_summaries(errors)})"
            )
            if errors:
                raise AssertionError(msg) from errors[0]
            raise AssertionError(msg)
        time.sleep(0.05)
    detail = (
        f" (serving thread alive={thread.is_alive()}, "
        f"errors={_error_summaries(errors)})"
        if thread is not None
        else ""
    )
    raise AssertionError(
        f"nothing is listening on 127.0.0.1:{port} after {timeout:.0f}s{detail}"
    )


def _install_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    installed: list[Any] = []
    real_install = application_mod.install_signal_handlers

    def spy(ctx: ApplicationContext, timeout: float) -> Any:
        installed.append((ctx, timeout))
        return real_install(ctx, timeout)

    monkeypatch.setattr(application_mod, "install_signal_handlers", spy)
    return installed


def test_run_serves_and_shuts_down_cleanly(
    webapp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _install_spy(monkeypatch)
    port = _free_port()
    results: dict[str, Any] = {}
    errors: list[BaseException] = []

    def work() -> None:
        try:
            results["ctx"] = PyleafApplication.run(
                "webapp",
                (
                    "--pyleaf.web.host=127.0.0.1",
                    f"--pyleaf.web.port={port}",
                    "--pyleaf.web.access-log=false",
                ),
                config_dir=webapp,
                banner=False,
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=work, name="pyleaf-serve", daemon=True)
    thread.start()

    server = None
    try:
        _wait_listening(port, thread, errors)

        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json() == {"status": "UP"}
        resp = httpx.get(f"http://127.0.0.1:{port}/hello", timeout=5)
        assert resp.status_code == 200
        assert resp.json() == {"msg": "hi"}

        module = importlib.import_module("webapp")
        ctx = module.captured["ctx"]
        client = ctx.get(httpx.AsyncClient)
        server = ctx.get(FastAPI).state.uvicorn_server
        server.should_exit = True
        thread.join(15)
    finally:
        if thread.is_alive() and server is not None:
            server.force_exit = True
            thread.join(5)

    assert errors == []
    assert not thread.is_alive(), "run() did not return after shutdown"
    # run() blocked on serving and returned the (now closed) context.
    assert results["ctx"] is ctx
    # The lifespan shutdown hook closed the context: @PreDestroy ran and
    # the shared HTTP client was closed while the loop was still alive.
    assert module.closed == ["x"]
    assert client.is_closed
    # In server mode uvicorn owns signals: boot's handler install is skipped.
    assert installed == []
    # The listening socket was released.
    assert not _listening(port)


def test_run_no_server_kwarg_returns_ctx_without_binding(
    webapp: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_signals: None,
) -> None:
    installed = _install_spy(monkeypatch)
    port = _free_port()

    ctx = PyleafApplication.run(
        "webapp",
        ("--pyleaf.web.host=127.0.0.1", f"--pyleaf.web.port={port}"),
        config_dir=webapp,
        banner=False,
        no_server=True,
    )

    assert len(installed) == 1  # plain CLI mode keeps the signal path
    assert not _listening(port)

    module = importlib.import_module("webapp")
    ctx.close()
    assert module.closed == ["x"]


def test_run_no_server_cli_arg_returns_without_binding(
    webapp: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_signals: None,
) -> None:
    installed = _install_spy(monkeypatch)
    port = _free_port()

    ctx = PyleafApplication.run(
        "webapp",
        (
            "--no-server",
            "--pyleaf.web.host=127.0.0.1",
            f"--pyleaf.web.port={port}",
        ),
        config_dir=webapp,
        banner=False,
    )

    assert len(installed) == 1
    assert not _listening(port)
    ctx.close()


def test_web_enabled_false_neither_serves_nor_creates_beans(
    webapp: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_signals: None,
) -> None:
    installed = _install_spy(monkeypatch)
    port = _free_port()

    ctx = PyleafApplication.run(
        "webapp",
        (
            "--pyleaf.web.enabled=false",
            "--pyleaf.web.host=127.0.0.1",
            f"--pyleaf.web.port={port}",
        ),
        config_dir=webapp,
        banner=False,
    )

    assert len(installed) == 1
    assert not _listening(port)
    with pytest.raises(NoSuchBean):
        ctx.get(FastAPI)
    ctx.close()
