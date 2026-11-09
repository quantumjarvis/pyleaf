"""Async ``AsyncSqlAlchemyTransactionManager`` tests (aiosqlite paths)."""

import pytest
from pyleaf.starter.sqlalchemy.tx import (
    AsyncSqlAlchemyTransactionManager,
    current_async_session,
)
from pyleaf.tx import (
    AsyncTransactionManager,
    NoTransactionError,
    TransactionTemplate,
    UnexpectedRollbackError,
)
from sa_models import Base, TxItem
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _create_tables(factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
    engine = factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _names(factory: async_sessionmaker) -> list[str]:  # type: ignore[type-arg]
    async with factory() as check:
        return sorted((await check.scalars(select(TxItem.name))).all())


async def test_manager_satisfies_async_transaction_manager_protocol(
    aiosqlite_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)
    assert isinstance(manager, AsyncTransactionManager)


async def test_required_commit_persists(
    aiosqlite_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    await _create_tables(aiosqlite_sessionmaker)
    manager = AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)
    tmpl = TransactionTemplate(manager)

    async def body() -> None:
        session = await current_async_session(aiosqlite_sessionmaker)
        session.add(TxItem(name="kept"))

    await tmpl.execute(body)

    assert await _names(aiosqlite_sessionmaker) == ["kept"]


async def test_required_rollback_discards(
    aiosqlite_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    await _create_tables(aiosqlite_sessionmaker)
    manager = AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)
    tmpl = TransactionTemplate(manager)

    async def body() -> None:
        session = await current_async_session(aiosqlite_sessionmaker)
        session.add(TxItem(name="dropped"))
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await tmpl.execute(body)

    assert await _names(aiosqlite_sessionmaker) == []


async def test_joined_required_boundary_shares_session(
    aiosqlite_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    await _create_tables(aiosqlite_sessionmaker)
    manager = AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)
    tmpl = TransactionTemplate(manager)
    seen: list[AsyncSession] = []

    async def body() -> None:
        seen.append(await current_async_session(aiosqlite_sessionmaker))

        async def inner() -> AsyncSession:
            return await current_async_session(aiosqlite_sessionmaker)

        seen.append(await tmpl.execute(inner))

    await tmpl.execute(body)

    assert len(seen) == 2
    assert seen[0] is seen[1]


async def test_current_async_session_resolves_by_manager_or_factory(
    aiosqlite_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    await _create_tables(aiosqlite_sessionmaker)
    manager = AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)
    tmpl = TransactionTemplate(manager)

    async def body() -> None:
        by_manager = await current_async_session(manager)
        by_factory = await current_async_session(aiosqlite_sessionmaker)
        assert by_manager is by_factory
        assert isinstance(by_manager, AsyncSession)

    await tmpl.execute(body)


async def test_current_async_session_unbound_raises_no_transaction(
    aiosqlite_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    with pytest.raises(NoTransactionError):
        await current_async_session(aiosqlite_sessionmaker)


async def test_current_async_session_require_tx_false_returns_none(
    aiosqlite_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    assert (
        await current_async_session(aiosqlite_sessionmaker, require_tx=False)
        is None
    )


async def test_joined_rollback_only_fails_outer_commit(
    aiosqlite_sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    await _create_tables(aiosqlite_sessionmaker)
    manager = AsyncSqlAlchemyTransactionManager(aiosqlite_sessionmaker)
    outer = TransactionTemplate(manager)
    inner = TransactionTemplate(manager)

    async def body() -> None:
        session = await current_async_session(aiosqlite_sessionmaker)
        session.add(TxItem(name="outer"))

        async def failing() -> None:
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            await inner.execute(failing)

    with pytest.raises(UnexpectedRollbackError):
        await outer.execute(body)

    assert await _names(aiosqlite_sessionmaker) == []
