"""REQUIRES_NEW via boundary suspend/resume — two real sessions (postgres).

Skips cleanly without docker. Both directions are pinned: an inner
REQUIRES_NEW commit survives the outer rollback, and an inner rollback
leaves the outer commit intact — proving the inner ran on an independent
session/connection while the outer was suspended.
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
from sqlalchemy.orm import Session, sessionmaker

_REQ_NEW = TransactionDefinition(propagation=Propagation.REQUIRES_NEW)


def _names(factory: sessionmaker) -> list[str]:  # type: ignore[type-arg]
    with factory() as check:
        return sorted(check.scalars(select(TxItem.name)).all())


def test_requires_new_inner_commit_survives_outer_rollback(
    pg_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    outer = TransactionTemplate(manager)
    inner = TransactionTemplate(manager, _REQ_NEW)
    pairs: list[tuple[Session, Session]] = []

    def body() -> None:
        outer_session = current_session(pg_sessionmaker)
        outer_session.add(TxItem(name="outer"))

        def inner_body() -> None:
            inner_session = current_session(pg_sessionmaker)
            pairs.append((outer_session, inner_session))
            inner_session.add(TxItem(name="inner"))
            inner_session.flush()

        inner.execute(inner_body)
        # The suspended outer binding is restored after the inner tx.
        assert current_session(pg_sessionmaker) is outer_session
        raise ValueError("outer")

    with pytest.raises(ValueError, match="outer"):
        outer.execute(body)

    # The inner ran on a different session and its commit persists.
    assert pairs[0][1] is not pairs[0][0]
    assert _names(pg_sessionmaker) == ["inner"]


def test_requires_new_inner_rollback_keeps_outer_row(
    pg_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(pg_sessionmaker)
    outer = TransactionTemplate(manager)
    inner = TransactionTemplate(manager, _REQ_NEW)

    def body() -> None:
        current_session(pg_sessionmaker).add(TxItem(name="outer"))

        def inner_body() -> None:
            inner_session = current_session(pg_sessionmaker)
            inner_session.add(TxItem(name="inner"))
            inner_session.flush()
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            inner.execute(inner_body)

    outer.execute(body)

    assert _names(pg_sessionmaker) == ["outer"]


async def test_requires_new_inner_commit_survives_outer_rollback_async(
    pg_async_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    factory = pg_async_sessionmaker
    manager = AsyncSqlAlchemyTransactionManager(factory)
    outer = TransactionTemplate(manager)
    inner = TransactionTemplate(manager, _REQ_NEW)
    pairs: list[tuple[AsyncSession, AsyncSession]] = []

    async def body() -> None:
        outer_session = await current_async_session(factory)
        outer_session.add(TxItem(name="outer"))

        async def inner_body() -> None:
            inner_session = await current_async_session(factory)
            pairs.append((outer_session, inner_session))
            inner_session.add(TxItem(name="inner"))
            await inner_session.flush()

        await inner.execute(inner_body)
        assert await current_async_session(factory) is outer_session
        raise ValueError("outer")

    with pytest.raises(ValueError, match="outer"):
        await outer.execute(body)

    assert pairs[0][1] is not pairs[0][0]
    async with factory() as check:
        names = sorted((await check.scalars(select(TxItem.name))).all())
    assert names == ["inner"]


async def test_requires_new_inner_rollback_keeps_outer_row_async(
    pg_async_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    factory = pg_async_sessionmaker
    manager = AsyncSqlAlchemyTransactionManager(factory)
    outer = TransactionTemplate(manager)
    inner = TransactionTemplate(manager, _REQ_NEW)

    async def body() -> None:
        session = await current_async_session(factory)
        session.add(TxItem(name="outer"))

        async def inner_body() -> None:
            inner_session = await current_async_session(factory)
            inner_session.add(TxItem(name="inner"))
            await inner_session.flush()
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            await inner.execute(inner_body)

    await outer.execute(body)

    async with factory() as check:
        names = sorted((await check.scalars(select(TxItem.name))).all())
    assert names == ["outer"]
