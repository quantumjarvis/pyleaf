"""``@Query`` contract tests: statement builders, returns hints, DML.

The no-string rule is structural: ``stmt_fn`` must be a callable, so a
string query can never reach execution. Statements are built with
SQLAlchemy constructs only.
"""

from typing import Any

import pytest
from data_ctx import build_async_ctx, build_sync_ctx
from data_models import Base, Item, ItemRepo
from pyleaf.core import ApplicationContext
from pyleaf.data import AsyncCrudRepository, CrudRepository, Query
from pyleaf.starter.sqlalchemy.tx import (
    AsyncSqlAlchemyTransactionManager,
    SqlAlchemyTransactionManager,
)
from pyleaf.tx import TransactionTemplate
from sqlalchemy import delete, func, select, update


class ItemQueryRepo(CrudRepository[Item, int], bean=True):
    """Sync repo with ``@Query``-decorated methods."""

    @Query(lambda model, name: select(model).where(model.name == name))
    def find_by_name(self, name: str) -> list[Item]: ...

    @Query(
        lambda model, name: select(model).where(model.name == name),
        returns="one",
    )
    def find_one_by_name(self, name: str) -> Item | None: ...

    @Query(lambda model: select(func.count()).select_from(model), returns="scalar")
    def count_all(self) -> int: ...

    @Query(
        lambda model, name, price: update(model)
        .where(model.name == name)
        .values(price=price),
        returns="scalar",
    )
    def set_price(self, name: str, price: int) -> int: ...

    @Query(lambda model, term: select(model).where(model.name.like(term)))
    def search(self, term: str) -> list[Item]: ...


class AsyncItemQueryRepo(AsyncCrudRepository[Item, int], bean=True):
    """Async repo with a ``@Query``-decorated coroutine method."""

    @Query(lambda model, name: select(model).where(model.name == name))
    async def find_by_name(self, name: str) -> list[Item]: ...

    @Query(lambda model: delete(model), returns="scalar")
    async def delete_all(self) -> int: ...


@pytest.fixture()
def qctx(sqlite_sessionmaker: Any) -> Any:
    ctx = build_sync_ctx(sqlite_sessionmaker, extra_repos=(ItemQueryRepo,))
    yield ctx
    ctx.close()


def _tmpl(ctx: ApplicationContext) -> TransactionTemplate:
    return TransactionTemplate(ctx.get(SqlAlchemyTransactionManager))


def _seed(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    with _tmpl(ctx).required():
        repo.save_all(
            [
                Item(name="apple", price=5),
                Item(name="apricot", price=7),
                Item(name="banana", price=3),
            ]
        )


def test_list_query_with_filter(qctx: ApplicationContext) -> None:
    _seed(qctx)
    repo = qctx.get(ItemQueryRepo)
    with _tmpl(qctx).required():
        found = repo.find_by_name("apple")
        assert [i.name for i in found] == ["apple"]
        assert repo.find_by_name("missing") == []


def test_returns_one(qctx: ApplicationContext) -> None:
    _seed(qctx)
    repo = qctx.get(ItemQueryRepo)
    with _tmpl(qctx).required():
        item = repo.find_one_by_name("banana")
        assert item is not None
        assert item.price == 3
        assert repo.find_one_by_name("missing") is None


def test_scalar_query(qctx: ApplicationContext) -> None:
    _seed(qctx)
    repo = qctx.get(ItemQueryRepo)
    with _tmpl(qctx).required():
        assert repo.count_all() == 3


def test_dml_returns_rowcount(qctx: ApplicationContext) -> None:
    _seed(qctx)
    repo = qctx.get(ItemQueryRepo)
    with _tmpl(qctx).required():
        assert repo.set_price("apple", 99) == 1
        assert repo.find_one_by_name("apple").price == 99
        assert repo.set_price("missing", 0) == 0


def test_stmt_fn_receives_kwargs(qctx: ApplicationContext) -> None:
    _seed(qctx)
    repo = qctx.get(ItemQueryRepo)
    with _tmpl(qctx).required():
        assert sorted(i.name for i in repo.search(term="a%")) == [
            "apple",
            "apricot",
        ]


def test_query_rejects_non_callable_stmt_fn() -> None:
    with pytest.raises(TypeError, match="callable"):
        Query("select * from items")  # type: ignore[arg-type]


def test_query_rejects_unknown_returns_hint() -> None:
    with pytest.raises(ValueError, match="returns"):
        Query(lambda model: select(model), returns="bogus")  # type: ignore[arg-type]


async def test_async_query_methods(aiosqlite: tuple[Any, Any]) -> None:
    class AsyncQRepo(AsyncItemQueryRepo):
        pass

    engine, sm = aiosqlite
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    ctx = build_async_ctx(sm, extra_repos=(AsyncQRepo,))
    try:
        repo = ctx.get(AsyncQRepo)
        tmpl = TransactionTemplate(ctx.get(AsyncSqlAlchemyTransactionManager))
        async with tmpl.required():
            # seed via the plain async repo bean already in the ctx
            from data_models import AsyncItemRepo

            seed_repo = ctx.get(AsyncItemRepo)
            await seed_repo.save_all(
                [Item(name="one", price=1), Item(name="two", price=2)]
            )
            assert [i.name for i in await repo.find_by_name("one")] == ["one"]
            assert await repo.delete_all() == 2
            assert await repo.find_by_name("one") == []
    finally:
        ctx.close()
