"""Signal-driven graceful shutdown for PyleafApplication."""

import importlib
import logging
import os
import signal
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from pyleaf.boot import PyleafApplication, install_signal_handlers, shutdown


class _Stub:
    """Stand-in context recording close() calls."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.closed = False

    def close(self) -> None:
        if self.delay:
            time.sleep(self.delay)
        self.closed = True


@pytest.fixture
def restore_signals() -> Iterator[None]:
    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGTERM, prev_term)
    signal.signal(signal.SIGINT, prev_int)


def test_installed_handler_closes_ctx(restore_signals: None) -> None:
    stub = _Stub()

    handler = install_signal_handlers(stub, 5.0)  # type: ignore[arg-type]

    assert signal.getsignal(signal.SIGTERM) is handler
    handler(signal.SIGTERM, None)
    assert stub.closed


def test_install_off_main_thread_degrades(restore_signals: None) -> None:
    """signal.signal raises ValueError off the main thread; install skips."""
    before = signal.getsignal(signal.SIGTERM)
    errors: list[Exception] = []

    def work() -> None:
        try:
            install_signal_handlers(_Stub(), 1.0)  # type: ignore[arg-type]
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(5)

    assert not thread.is_alive()
    assert errors == []
    assert signal.getsignal(signal.SIGTERM) == before


def test_shutdown_timeout_does_not_hang(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A close() slower than the timeout logs and still returns."""
    stub = _Stub(delay=1.0)
    start = time.monotonic()

    with caplog.at_level(logging.WARNING):
        shutdown(stub, 0.05)  # type: ignore[arg-type]

    assert time.monotonic() - start < 0.9
    assert not stub.closed
    assert any("timed out" in record.message for record in caplog.records)


def test_shutdown_waits_for_close_within_timeout() -> None:
    stub = _Stub(delay=0.02)

    shutdown(stub, 5.0)  # type: ignore[arg-type]

    assert stub.closed


def test_sigterm_after_run_closes_ctx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_signals: None,
) -> None:
    """run() installs the handler on the main thread; SIGTERM -> ctx.close()."""
    pkg = tmp_path / "sigapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from pyleaf.core import Component, PreDestroy\n"
        "closed = []\n"
        "@Component\n"
        "class Holder:\n"
        "    @PreDestroy\n"
        "    def bye(self):\n"
        "        closed.append('x')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    PyleafApplication.run(
        "sigapp", (), config_dir=tmp_path, banner=False, no_server=True
    )
    sigapp = importlib.import_module("sigapp")

    os.kill(os.getpid(), signal.SIGTERM)
    for _ in range(200):
        if sigapp.closed:
            break
        time.sleep(0.01)

    assert sigapp.closed == ["x"]


def test_run_off_main_thread_still_returns_ctx(tmp_path: Path) -> None:
    """run() in a worker thread cannot install handlers but must not raise."""
    pkg = tmp_path / "offapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    import sys

    sys.path.insert(0, str(tmp_path))
    try:
        importlib.invalidate_caches()
        # Eager main-thread import: run() scans "offapp" in the worker
        # thread, and a first import off the main thread can race with
        # concurrent sys.modules mutation (KeyError inside _find_and_load).
        sys.modules.pop("offapp", None)
        importlib.import_module("offapp")
        results: list[object] = []
        errors: list[Exception] = []

        def work() -> None:
            try:
                results.append(
                    PyleafApplication.run(
                        "offapp",
                        (),
                        config_dir=tmp_path,
                        banner=False,
                        no_server=True,
                    )
                )
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=work)
        thread.start()
        thread.join(10)
    finally:
        sys.path.remove(str(tmp_path))

    assert errors == []
    assert len(results) == 1
