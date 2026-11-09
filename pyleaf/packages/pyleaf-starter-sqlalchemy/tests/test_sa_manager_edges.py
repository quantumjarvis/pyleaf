"""Manager-level edge cases for ``pyleaf.starter.sqlalchemy.tx`` (PYL-42).

The template-driven paths live in the other ``test_sa_*`` files; these
pin what happens at the seams: standalone (boundary-less) use, begin-time
failure cleanup, dialect-gated option emission, the missing-greenlet
guard, and the module's ``sqlalchemy.ext.asyncio`` import fallback.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import Any

import pytest
import sqlalchemy.exc
from pyleaf.starter.sqlalchemy import tx as tx_mod
from pyleaf.starter.sqlalchemy.tx import (
    AsyncSqlAlchemyTransactionManager,
    SqlAlchemyTransactionManager,
    current_async_session,
    current_session,
)
from pyleaf.tx import (
    Isolation,
    TransactionDefinition,
    TransactionError,
    TransactionTemplate,
)
from pyleaf.tx.sync import TransactionSynchronizationManager as TSM
from sa_models import Base, TxItem
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker


def _names(factory: sessionmaker) -> list[str]:  # type: ignore[type-arg]
    with factory() as check:
        return sorted(check.scalars(select(TxItem.name)).all())


# -- module import fallback -----------------------------------------------------


def test_module_loads_without_sqlalchemy_asyncio() -> None:
    """When ``sqlalchemy.ext.asyncio`` can't import (no greenlet), the
    module still loads — only the async manager becomes unusable.

    The source is exec'd in a throwaway module namespace (NOT reloaded —
    a reload would swap class objects under every already-imported name
    and break ``_resource_key``'s isinstance resolution for the rest of
    the suite).
    """
    spec = importlib.util.spec_from_file_location(
        "sa_tx_import_probe", tx_mod.__file__
    )
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    saved = sys.modules.get("sqlalchemy.ext.asyncio")
    # A None sys.modules entry makes `import sqlalchemy.ext.asyncio`
    # raise ImportError — the documented no-greenlet environment.
    sys.modules["sqlalchemy.ext.asyncio"] = None
    # The probe must be importable-by-name: @dataclass resolves string
    # annotations through sys.modules[cls.__module__].
    sys.modules[probe.__name__] = probe
    try:
        spec.loader.exec_module(probe)
        assert probe.__dict__["_sa_asyncio"] is None
    finally:
        sys.modules.pop(probe.__name__, None)
        if saved is None:
            sys.modules.pop("sqlalchemy.ext.asyncio", None)
        else:
            sys.modules["sqlalchemy.ext.asyncio"] = saved
    assert tx_mod._sa_asyncio is not None


# -- standalone use (no boundary) ------------------------------------------------


def test_standalone_begin_commit_manages_tsm(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    """begin() outside a TransactionTemplate self-initializes the TSM;
    commit() unbinds, closes the session and clears it again."""
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    assert not TSM.is_active()

    status = manager.begin(TransactionDefinition())
    assert status.is_new
    assert TSM.is_active()

    session = current_session(sqlite_sessionmaker)
    session.add(TxItem(name="direct"))

    manager.commit(status)
    assert not TSM.is_active()
    assert TSM.get_resource(sqlite_sessionmaker) is None
    assert _names(sqlite_sessionmaker) == ["direct"]


def test_standalone_begin_rollback_manages_tsm(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    status = manager.begin(TransactionDefinition())
    current_session(sqlite_sessionmaker).add(TxItem(name="dropped"))

    manager.rollback(status)
    assert not TSM.is_active()
    assert _names(sqlite_sessionmaker) == []


# -- begin failure cleanup -------------------------------------------------------


def test_begin_failure_closes_session_and_clears_tsm(tmp_path: Any) -> None:
    """A session.begin() that explodes propagates and leaves no TSM
    residue — the session is closed and the self-initialized context is
    cleared."""
    engine = create_engine(f"sqlite:///{tmp_path}/fail.db")
    Base.metadata.create_all(engine)

    class _ExplodingSession(Session):
        def begin(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("begin exploded")

    factory = sessionmaker(bind=engine, class_=_ExplodingSession)
    manager = SqlAlchemyTransactionManager(factory)
    with pytest.raises(RuntimeError, match="begin exploded"):
        manager.begin(TransactionDefinition())
    assert not TSM.is_active()
    assert TSM.get_resource(factory) is None
    engine.dispose()


# -- dialect-gated option emission ------------------------------------------------


def test_isolation_clause_emitted_on_ansi_dialect(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On non-postgres ANSI dialects the isolation is a standalone
    ``SET SESSION TRANSACTION ...`` statement.

    The dialect set is patched to treat sqlite as ANSI-capable and the
    session records instead of executing the SET — the emitted statement
    text is what matters, no MySQL server required.
    """
    monkeypatch.setattr(
        tx_mod, "_ISOLATION_DIALECTS", frozenset({"sqlite"})
    )
    captured: list[str] = []
    engine = sqlite_sessionmaker.kw["bind"]

    class _RecordingSession(Session):
        def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
            sql = str(statement)
            if sql.startswith("SET ") or "set_config" in sql:
                captured.append(sql)
                return None
            return super().execute(statement, *args, **kwargs)

    factory = sessionmaker(bind=engine, class_=_RecordingSession)
    manager = SqlAlchemyTransactionManager(factory)
    tmpl = TransactionTemplate(
        manager,
        TransactionDefinition(isolation=Isolation.SERIALIZABLE),
    )
    tmpl.execute(lambda: None)
    assert captured == [
        "SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE"
    ]


# -- async edges -----------------------------------------------------------------


async def _create_tables(factory: Any) -> None:
    engine = factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _async_names(factory: Any) -> list[str]:
    async with factory() as check:
        return sorted((await check.scalars(select(TxItem.name))).all())


async def test_async_ctor_requires_sqlalchemy_asyncio(
    aiosqlite_sessionmaker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``sqlalchemy.ext.asyncio`` (no greenlet) construction
    fails fast with TransactionError, not an ImportError at import."""
    monkeypatch.setattr(tx_mod, "_sa_asyncio", None)
    with pytest.raises(TransactionError, match="asyncio"):
        AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)


async def test_async_standalone_begin_commit_manages_tsm(
    aiosqlite_sessionmaker: Any,
) -> None:
    """Async mirror of the sync standalone test: self-init, then clear."""
    await _create_tables(aiosqlite_sessionmaker)
    manager = AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)
    assert not TSM.is_active()

    status = await manager.begin(TransactionDefinition())
    assert status.is_new
    assert TSM.is_active()

    session = await current_async_session(aiosqlite_sessionmaker)
    session.add(TxItem(name="adirect"))

    await manager.commit(status)
    assert not TSM.is_active()
    assert TSM.get_resource(aiosqlite_sessionmaker) is None
    assert await _async_names(aiosqlite_sessionmaker) == ["adirect"]


async def test_async_begin_failure_closes_session_and_clears_tsm(
    aiosqlite_sessionmaker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An _apply_options failure (sqlite rejecting the ANSI SET) must
    close the session, clear the self-initialized TSM and propagate."""
    monkeypatch.setattr(
        tx_mod, "_ISOLATION_DIALECTS", frozenset({"sqlite"})
    )
    manager = AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)
    # sqlite has no SET statement -> OperationalError inside
    # _apply_options -> the begin() except-path runs.
    with pytest.raises(sqlalchemy.exc.OperationalError):
        await manager.begin(
            TransactionDefinition(isolation=Isolation.SERIALIZABLE)
        )
    assert not TSM.is_active()
    assert TSM.get_resource(aiosqlite_sessionmaker) is None


async def test_async_read_only_guard_blocks_flush_and_is_removed(
    aiosqlite_sessionmaker: Any,
) -> None:
    """read_only installs the before_flush guard on the sync session and
    rollback's _release removes it."""
    await _create_tables(aiosqlite_sessionmaker)
    manager = AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)
    status = await manager.begin(TransactionDefinition(read_only=True))
    session = await current_async_session(aiosqlite_sessionmaker)
    session.add(TxItem(name="blocked"))
    with pytest.raises(TransactionError, match="read-only"):
        await session.flush()

    await manager.rollback(status)
    assert not TSM.is_active()
    assert await _async_names(aiosqlite_sessionmaker) == []


async def test_async_options_on_postgres(
    pg_async_sessionmaker: Any,
) -> None:
    """Real asyncpg: SET TRANSACTION ISOLATION LEVEL ... READ ONLY plus
    statement_timeout — the full async _apply_options postgres block."""
    manager = AsyncSqlAlchemyTransactionManager(pg_async_sessionmaker)
    tmpl = TransactionTemplate(
        manager,
        TransactionDefinition(
            isolation=Isolation.SERIALIZABLE,
            read_only=True,
            timeout_s=2.5,
        ),
    )

    async def body() -> tuple[str, str, str]:
        session = await current_async_session(pg_async_sessionmaker)
        iso = str(
            (await session.execute(text("SHOW transaction_isolation")))
            .scalar()
        )
        ro = str(
            (await session.execute(text("SHOW transaction_read_only")))
            .scalar()
        )
        timeout = str(
            (await session.execute(text("SHOW statement_timeout")))
            .scalar()
        )
        return iso, ro, timeout

    iso, ro, timeout = await tmpl.execute(body)
    assert iso == "serializable"
    assert ro == "on"
    assert timeout in ("2500ms", "2.5s")
