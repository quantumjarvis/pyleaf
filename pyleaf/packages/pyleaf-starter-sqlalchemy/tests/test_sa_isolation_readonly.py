"""Isolation / read-only / timeout semantics.

Postgres cases run on the container; the sqlite cases pin the documented
best-effort behavior (isolation ignored on sqlite, the read-only flush
guard is dialect-independent).
"""

import pytest
from pyleaf.starter.sqlalchemy.tx import (
    SqlAlchemyTransactionManager,
    current_session,
)
from pyleaf.tx import (
    Isolation,
    TransactionDefinition,
    TransactionError,
    TransactionTemplate,
)
from sa_models import TxItem
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker


def _names(factory: sessionmaker) -> list[str]:  # type: ignore[type-arg]
    with factory() as check:
        return sorted(check.scalars(select(TxItem.name)).all())


def test_isolation_serializable_on_postgres(
    pg_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    tmpl = TransactionTemplate(
        manager, TransactionDefinition(isolation=Isolation.SERIALIZABLE)
    )

    def body() -> str:
        session = current_session(pg_sessionmaker)
        return str(
            session.execute(text("SHOW transaction_isolation")).scalar()
        )

    assert tmpl.execute(body) == "serializable"


def test_read_only_flag_on_postgres(
    pg_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    tmpl = TransactionTemplate(
        manager, TransactionDefinition(read_only=True)
    )

    def body() -> str:
        session = current_session(pg_sessionmaker)
        return str(
            session.execute(text("SHOW transaction_read_only")).scalar()
        )

    assert tmpl.execute(body) == "on"


def test_read_only_rejects_writes_on_postgres(
    pg_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    tmpl = TransactionTemplate(
        manager, TransactionDefinition(read_only=True)
    )

    def body() -> None:
        session = current_session(pg_sessionmaker)
        session.add(TxItem(name="nope"))
        session.flush()  # the before_flush guard must raise

    with pytest.raises(TransactionError, match="read-only"):
        tmpl.execute(body)

    assert _names(pg_sessionmaker) == []


def test_read_only_rejects_writes_on_sqlite(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    """The before_flush guard is dialect-independent (pinned)."""
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    tmpl = TransactionTemplate(
        manager, TransactionDefinition(read_only=True)
    )

    def body() -> None:
        session = current_session(sqlite_sessionmaker)
        session.add(TxItem(name="nope"))
        session.flush()

    with pytest.raises(TransactionError, match="read-only"):
        tmpl.execute(body)

    assert _names(sqlite_sessionmaker) == []


def test_isolation_on_sqlite_is_best_effort_noop(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    """sqlite ignores a non-default isolation (documented); tx still works."""
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    tmpl = TransactionTemplate(
        manager, TransactionDefinition(isolation=Isolation.SERIALIZABLE)
    )

    tmpl.execute(
        lambda: current_session(sqlite_sessionmaker).add(TxItem(name="ok"))
    )

    assert _names(sqlite_sessionmaker) == ["ok"]


def test_timeout_sets_statement_timeout_on_postgres(
    pg_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    tmpl = TransactionTemplate(manager, TransactionDefinition(timeout_s=2.5))

    def body() -> str:
        session = current_session(pg_sessionmaker)
        return str(session.execute(text("SHOW statement_timeout")).scalar())

    value = tmpl.execute(body)
    assert value in ("2500ms", "2.5s")
