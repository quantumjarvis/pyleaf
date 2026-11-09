"""NESTED propagation → real database savepoints (postgres container).

Skips cleanly when docker/testcontainers are unavailable. The ``flush``
calls inside inner bodies are deliberate: they emit INSERT inside the
savepoint so ROLLBACK TO SAVEPOINT is exercised at the database level,
not just via ORM state restore.
"""

import pytest
from pyleaf.starter.sqlalchemy.tx import (
    AsyncSqlAlchemyTransactionManager,
    SqlAlchemyTransactionManager,
    current_async_session,
    current_session,
)
from pyleaf.tx import Propagation, TransactionDefinition, TransactionTemplate
from sa_models import TxItem
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker

_NESTED = TransactionDefinition(propagation=Propagation.NESTED)


def _names(factory: sessionmaker) -> list[str]:  # type: ignore[type-arg]
    with factory() as check:
        return sorted(check.scalars(select(TxItem.name)).all())


def test_nested_inner_rollback_keeps_outer_row(
    pg_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    outer = TransactionTemplate(manager)
    nested = TransactionTemplate(manager, _NESTED)

    def body() -> None:
        current_session(pg_sessionmaker).add(TxItem(name="outer"))

        def inner() -> None:
            session = current_session(pg_sessionmaker)
            session.add(TxItem(name="inner"))
            session.flush()  # INSERT inside the savepoint
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            nested.execute(inner)

    outer.execute(body)

    assert _names(pg_sessionmaker) == ["outer"]


def test_nested_commit_then_outer_rollback_loses_both(
    pg_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    outer = TransactionTemplate(manager)
    nested = TransactionTemplate(manager, _NESTED)

    def body() -> None:
        current_session(pg_sessionmaker).add(TxItem(name="outer"))

        def inner() -> None:
            session = current_session(pg_sessionmaker)
            session.add(TxItem(name="inner"))
            session.flush()

        nested.execute(inner)
        raise ValueError("outer")

    with pytest.raises(ValueError, match="outer"):
        outer.execute(body)

    assert _names(pg_sessionmaker) == []


async def test_nested_inner_rollback_keeps_outer_row_async(
    pg_async_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    factory = pg_async_sessionmaker
    manager = AsyncSqlAlchemyTransactionManager(factory)
    outer = TransactionTemplate(manager)
    nested = TransactionTemplate(manager, _NESTED)

    async def body() -> None:
        session = await current_async_session(factory)
        session.add(TxItem(name="outer"))

        async def inner() -> None:
            inner_session = await current_async_session(factory)
            # NESTED joins the same bound session — no rebind.
            assert inner_session is session
            inner_session.add(TxItem(name="inner"))
            await inner_session.flush()
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            await nested.execute(inner)

    await outer.execute(body)

    async with factory() as check:
        names = sorted((await check.scalars(select(TxItem.name))).all())
    assert names == ["outer"]


async def test_nested_commit_then_outer_rollback_loses_both_async(
    pg_async_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    factory = pg_async_sessionmaker
    manager = AsyncSqlAlchemyTransactionManager(factory)
    outer = TransactionTemplate(manager)
    nested = TransactionTemplate(manager, _NESTED)
    sessions: list[AsyncSession] = []

    async def body() -> None:
        session = await current_async_session(factory)
        sessions.append(session)
        session.add(TxItem(name="outer"))

        async def inner() -> None:
            inner_session = await current_async_session(factory)
            inner_session.add(TxItem(name="inner"))
            await inner_session.flush()

        await nested.execute(inner)
        raise ValueError("outer")

    with pytest.raises(ValueError, match="outer"):
        await outer.execute(body)

    # The savepoint status used the same outer session throughout.
    async with factory() as check:
        names = sorted((await check.scalars(select(TxItem.name))).all())
    assert names == []
