"""Pool no-leak: checked-out connections return to 0 on every path.

The postgres cases run against the container's QueuePool; the sqlite
case (explicit QueuePool) always runs.
"""

import pytest
from pyleaf.starter.sqlalchemy.tx import (
    SqlAlchemyTransactionManager,
    current_session,
)
from pyleaf.tx import Propagation, TransactionDefinition, TransactionTemplate
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

_REQ_NEW = TransactionDefinition(propagation=Propagation.REQUIRES_NEW)


def test_pool_checkout_returns_to_zero_on_commit(
    pg_engine: Engine, pg_sessionmaker: sessionmaker  # type: ignore[type-arg]
) -> None:
    pool = pg_engine.pool
    assert pool.checkedout() == 0
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    tmpl = TransactionTemplate(manager)
    seen: list[int] = []

    def body() -> None:
        current_session(pg_sessionmaker).execute(text("SELECT 1"))
        seen.append(pool.checkedout())

    tmpl.execute(body)

    assert seen == [1]
    assert pool.checkedout() == 0


def test_pool_checkout_returns_to_zero_on_rollback(
    pg_engine: Engine, pg_sessionmaker: sessionmaker  # type: ignore[type-arg]
) -> None:
    pool = pg_engine.pool
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    tmpl = TransactionTemplate(manager)

    def body() -> None:
        current_session(pg_sessionmaker).execute(text("SELECT 1"))
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        tmpl.execute(body)

    assert pool.checkedout() == 0


def test_requires_new_releases_both_connections(
    pg_engine: Engine, pg_sessionmaker: sessionmaker  # type: ignore[type-arg]
) -> None:
    """Outer + suspended-inner connections both check back in."""
    pool = pg_engine.pool
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    outer = TransactionTemplate(manager)
    inner = TransactionTemplate(manager, _REQ_NEW)
    high_water: list[int] = []

    def body() -> None:
        current_session(pg_sessionmaker).execute(text("SELECT 1"))

        def inner_body() -> None:
            inner_session = current_session(pg_sessionmaker)
            inner_session.execute(text("SELECT 1"))
            high_water.append(pool.checkedout())

        inner.execute(inner_body)

    outer.execute(body)

    assert high_water == [2]
    assert pool.checkedout() == 0


def test_sqlite_queuepool_no_leak(tmp_path: object) -> None:
    """Always-runs variant: explicit QueuePool on sqlite."""
    engine = create_engine("sqlite://", poolclass=QueuePool)
    factory = sessionmaker(bind=engine)
    manager = SqlAlchemyTransactionManager(factory)
    tmpl = TransactionTemplate(manager)

    def body() -> None:
        current_session(factory).execute(text("SELECT 1"))
        assert engine.pool.checkedout() == 1

    tmpl.execute(body)
    assert engine.pool.checkedout() == 0

    def bad() -> None:
        current_session(factory).execute(text("SELECT 1"))
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        tmpl.execute(bad)
    assert engine.pool.checkedout() == 0

    engine.dispose()
