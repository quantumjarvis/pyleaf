"""DataAutoConfiguration: entry point, tx marker, repo e2e (WI 3.6, PYL-37)."""

from pathlib import Path

import pytest
from data_fixture_app.models import Base, Note
from data_fixture_app.repo import NoteRepo
from data_fixture_app.service import NoteService
from pyleaf.boot import (
    discover_autoconfigs,
    is_autoconfiguration,
    register_autoconfigs,
)
from pyleaf.core import (
    ApplicationContext,
    Configuration,
    Environment,
    NoSuchBean,
    PropertySource,
)
from pyleaf.data.autoconfig import (
    ENABLE_TX_ATTR,
    DataAutoConfiguration,
    EnableTransactionManagement,
    TransactionManagementMarker,
)
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

_EP_NAMES = {"tx", "sqlalchemy", "data"}


def _ctx(
    *classes: type, values: dict[str, object], scan: list[str] | None = None
) -> ApplicationContext:
    ctx = ApplicationContext(
        scan=scan,
        environment=Environment(sources=[PropertySource("test", values)]),
    )
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def _sqlite_url(tmp_path: Path) -> dict[str, object]:
    return {"app.datasource.url": f"sqlite:///{tmp_path}/app.db"}


def test_entry_point_loads_data_autoconfiguration() -> None:
    """The ``data`` entry point resolves to ``DataAutoConfiguration``."""
    discovered = discover_autoconfigs()

    assert "data" in discovered
    assert discovered["data"] is DataAutoConfiguration
    assert is_autoconfiguration(DataAutoConfiguration)


def test_enable_transaction_management_marker(tmp_path: Path) -> None:
    """``@EnableTransactionManagement`` on a user config registers the
    detectable marker bean (tx support itself is already on via the
    ``tx`` auto-configuration's interceptor — the marker is the explicit
    opt-in record)."""

    @Configuration
    @EnableTransactionManagement
    class TxOptIn:
        pass

    ctx = _ctx(TxOptIn, DataAutoConfiguration, values=_sqlite_url(tmp_path))

    assert getattr(TxOptIn, ENABLE_TX_ATTR, False) is True
    marker = ctx.get(TransactionManagementMarker)
    assert isinstance(marker, TransactionManagementMarker)
    assert ctx.get_bean("transactionManagementMarker") is marker


def test_marker_absent_without_opt_in(tmp_path: Path) -> None:
    """No ``@EnableTransactionManagement`` -> no marker bean; the def is
    dropped with a condition-report reason."""
    ctx = _ctx(DataAutoConfiguration, values=_sqlite_url(tmp_path))

    with pytest.raises(NoSuchBean):
        ctx.get(TransactionManagementMarker)

    entries = {e.bean_name: e for e in ctx.condition_report.entries}
    entry = entries["transactionManagementMarker"]
    assert entry.matched is False
    assert any("EnableTransactionManagement" in r for r in entry.reasons)


def test_enable_marker_is_a_noop_friendly_opt_in(tmp_path: Path) -> None:
    """The marker alone must not break a context with no tx beans at all —
    honest no-op semantics like Spring's (where spring-tx autoconfigure
    also enables the interceptor implicitly)."""

    @Configuration
    @EnableTransactionManagement
    class OptInOnly:
        pass

    ctx = _ctx(OptInOnly, DataAutoConfiguration, values={})
    assert isinstance(ctx.get(TransactionManagementMarker), TransactionManagementMarker)


def test_repo_e2e_scan_plus_starter_ep_plus_transactional(
    tmp_path: Path,
) -> None:
    """Package scan + ``pyleaf.autoconfigure`` entry points + a stereotype
    repository injected into a ``@Transactional`` service — the 3.5
    ``bean=True`` stereotype works end to end with no extra hooks."""
    ctx = ApplicationContext(
        scan=["data_fixture_app"],
        environment=Environment(
            sources=[PropertySource("test", _sqlite_url(tmp_path))]
        ),
    )
    discovered = {
        name: loaded
        for name, loaded in discover_autoconfigs().items()
        if name in _EP_NAMES
    }
    register_autoconfigs(ctx, discovered)
    ctx.refresh()

    # Tables on the starter-provided engine, then a @Transactional write
    # through the scanned repository bean.
    Base.metadata.create_all(ctx.get(Engine))
    ctx.get(NoteService).create("hello")

    factory = ctx.get(sessionmaker)
    with factory() as session:
        stmt = select(func.count()).select_from(Note)
        assert int(session.scalar(stmt) or 0) == 1

    # The scanned repo is a real singleton bean, injectable on its own.
    assert ctx.get(NoteRepo) is ctx.get(NoteRepo)
