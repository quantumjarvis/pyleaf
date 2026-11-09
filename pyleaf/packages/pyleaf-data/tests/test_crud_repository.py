"""Sync + async CrudRepository contract tests (sqlite / aiosqlite).

Repositories are resolved as real beans from an ApplicationContext
(``ctx.get`` / constructor injection) and every op runs inside a
``TransactionTemplate`` boundary over the starter's transaction manager,
so the ambient-tx binding via ``current_session`` is exercised.
"""

import statistics
import time
from typing import Any

import pytest
from data_models import Base, Item, ItemRepo
from pyleaf.core import ApplicationContext, Component
from pyleaf.data import CrudRepository
from pyleaf.starter.sqlalchemy.tx import (
    AsyncSqlAlchemyTransactionManager,
    SqlAlchemyTransactionManager,
    current_async_session,
    current_session,
)
from pyleaf.tx import NoTransactionError, TransactionTemplate


def _template(ctx: ApplicationContext) -> TransactionTemplate:
    return TransactionTemplate(ctx.get(SqlAlchemyTransactionManager))


def _async_template(ctx: ApplicationContext) -> TransactionTemplate:
    return TransactionTemplate(ctx.get(AsyncSqlAlchemyTransactionManager))


def _seed(ctx: ApplicationContext, items: list[Item]) -> None:
    """Insert rows and commit so later transactions see them."""
    repo = ctx.get(ItemRepo)
    with _template(ctx).required():
        repo.save_all(items)


def _names(ctx: ApplicationContext) -> list[str]:
    repo = ctx.get(ItemRepo)
    with _template(ctx).required():
        return sorted(item.name for item in repo.find_all())


# -- bean wiring -------------------------------------------------------------


def test_repo_resolves_as_injectable_bean(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    assert isinstance(repo, ItemRepo)

    @Component
    class _Consumer:
        def __init__(self, item_repo: ItemRepo) -> None:
            self.item_repo = item_repo

    ctx.register(_Consumer)
    assert ctx.get(_Consumer).item_repo is repo


def test_unparametrized_subclass_raises_type_error() -> None:
    with pytest.raises(TypeError, match="concrete type arguments"):

        class _BadRepo(CrudRepository):
            pass


def test_non_class_model_argument_raises_type_error() -> None:
    from collections.abc import Callable

    with pytest.raises(TypeError, match=r"concrete.*mapped class"):

        class _BadRepo(CrudRepository[Callable[[], int], int]):  # type: ignore[valid-type]
            pass


def test_plain_subclass_inherits_model(ctx: ApplicationContext) -> None:
    class _SpecialRepo(ItemRepo):
        pass

    assert _SpecialRepo._model is Item


# -- CRUD --------------------------------------------------------------------


def test_save_insert_then_find_by_id(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    with _template(ctx).required():
        # save() merges without flushing — the pk is assigned on flush.
        session = current_session(ctx.get(SqlAlchemyTransactionManager))
        saved = repo.save(Item(name="alpha", price=10))
        session.flush()
        assert saved.id is not None
        found = repo.find_by_id(saved.id)
        assert found is not None
        assert found.name == "alpha"


def test_save_update_uses_merge(ctx: ApplicationContext) -> None:
    """save() on a detached entity with a set pk UPDATEs (merge semantics)."""
    repo = ctx.get(ItemRepo)
    with _template(ctx).required():
        saved = repo.save(Item(name="before", price=1))
        current_session(ctx.get(SqlAlchemyTransactionManager)).flush()
        ident = int(saved.id)
    with _template(ctx).required():
        merged = repo.save(Item(id=ident, name="after", price=2))
        assert merged.id == ident
    with _template(ctx).required():
        found = repo.find_by_id(ident)
        assert found is not None
        assert found.name == "after"
        assert found.price == 2
        assert repo.count() == 1


def test_save_all_returns_managed_instances(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    with _template(ctx).required():
        saved = repo.save_all(
            [Item(name="a", price=1), Item(name="b", price=2)]
        )
        current_session(ctx.get(SqlAlchemyTransactionManager)).flush()
        assert len(saved) == 2
        assert all(item.id is not None for item in saved)
    assert _names(ctx) == ["a", "b"]


def test_find_by_id_missing_returns_none(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    with _template(ctx).required():
        assert repo.find_by_id(999) is None


def test_exists_by_id_and_count(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    _seed(ctx, [Item(name="x", price=1)])
    with _template(ctx).required():
        assert repo.count() == 1
        item = repo.find_all()[0]
        assert repo.exists_by_id(item.id)
        assert not repo.exists_by_id(item.id + 100)


def test_delete_entity(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    _seed(ctx, [Item(name="victim", price=1)])
    with _template(ctx).required():
        item = repo.find_all()[0]
        repo.delete(item)
        assert repo.count() == 0
    assert _names(ctx) == []


def test_delete_rolls_back_with_tx(ctx: ApplicationContext) -> None:
    """delete() flushes inside the tx — a rollback undoes it."""
    repo = ctx.get(ItemRepo)
    _seed(ctx, [Item(name="survivor", price=1)])
    tmpl = _template(ctx)
    with pytest.raises(ValueError, match="abort"), tmpl.required():
        repo.delete(repo.find_all()[0])
        raise ValueError("abort")
    with tmpl.required():
        assert repo.count() == 1


def test_delete_by_id(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    _seed(ctx, [Item(name="gone", price=1)])
    with _template(ctx).required():
        ident = repo.find_all()[0].id
    with _template(ctx).required():
        assert repo.delete_by_id(ident) is True
    with _template(ctx).required():
        assert repo.delete_by_id(ident) is False
    assert _names(ctx) == []


def test_find_all_returns_everything(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    _seed(ctx, [Item(name="a", price=1), Item(name="b", price=2)])
    with _template(ctx).required():
        assert sorted(i.name for i in repo.find_all()) == ["a", "b"]


def test_operations_require_ambient_tx(ctx: ApplicationContext) -> None:
    """No ambient transaction -> NoTransactionError propagates."""
    repo = ctx.get(ItemRepo)
    with pytest.raises(NoTransactionError):
        repo.find_by_id(1)
    with pytest.raises(NoTransactionError):
        repo.count()
    with pytest.raises(NoTransactionError):
        repo.save(Item(name="x", price=1))


# -- perf: same-run ratio vs raw session.get -----------------------------------


def test_find_by_id_overhead_vs_raw_session_get(ctx: ApplicationContext) -> None:
    """``repo.find_by_id`` must stay under 1.05x a raw ``session.get``.

    Same-run ratio: both loops do ``expire_all()`` + one get of the same
    pk, so every iteration issues a real SELECT — the only difference is
    the repository's session-resolution + delegation overhead. Median of
    alternating batches keeps drift out of the ratio.
    """
    repo = ctx.get(ItemRepo)
    manager = ctx.get(SqlAlchemyTransactionManager)
    with TransactionTemplate(manager).required():
        session = current_session(manager)
        saved = repo.save(Item(name="perf", price=1))
        session.flush()
        ident = int(saved.id)

        rounds, iters = 7, 40
        ratios: list[float] = []
        for _ in range(rounds):
            start = time.perf_counter()
            for _ in range(iters):
                session.expire_all()
                session.get(Item, ident)
            raw = time.perf_counter() - start

            start = time.perf_counter()
            for _ in range(iters):
                session.expire_all()
                repo.find_by_id(ident)
            via_repo = time.perf_counter() - start
            ratios.append(via_repo / raw)

        ratio = statistics.median(ratios)
        assert ratio < 1.05, f"find_by_id overhead {ratio:.3f}x >= 1.05x"


# -- async --------------------------------------------------------------------


async def test_async_crud_roundtrip(
    actx: ApplicationContext, aiosqlite: tuple[Any, Any]
) -> None:
    from data_models import AsyncItemRepo

    engine, sm = aiosqlite
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    repo = actx.get(AsyncItemRepo)
    tmpl = _async_template(actx)
    async with tmpl.required():
        saved = await repo.save(Item(name="async-a", price=7))
        await (await current_async_session(sm)).flush()
        assert saved.id is not None
        assert await repo.exists_by_id(saved.id)
        assert (await repo.find_by_id(saved.id)).name == "async-a"
        assert await repo.count() == 1
        assert await repo.delete_by_id(saved.id) is True
        assert await repo.count() == 0


async def test_async_operations_require_ambient_tx(
    actx: ApplicationContext, aiosqlite: tuple[Any, Any]
) -> None:
    from data_models import AsyncItemRepo

    engine, _sm = aiosqlite
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    repo = actx.get(AsyncItemRepo)
    with pytest.raises(NoTransactionError):
        await repo.find_by_id(1)
    with pytest.raises(NoTransactionError):
        await repo.count()
