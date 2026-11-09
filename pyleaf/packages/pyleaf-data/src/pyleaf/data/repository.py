"""Typed CrudRepository / AsyncCrudRepository (WI 3.5, PYL-36).

``CrudRepository[T, ID]`` is a *concrete generic base*, not a protocol —
apps subclass it with real type arguments::

    class ItemRepo(CrudRepository[Item, int], bean=True): ...

``__init_subclass__`` extracts ``T`` from the ``__orig_bases__`` generic
alias into ``cls._model``; a subclass that leaves the base
unparametrized (or parametrizes it with a non-class) gets a ``TypeError``
at class-creation time. Intermediate still-generic bases
(``class Base(CrudRepository[T, ID], Generic[T, ID])``) are allowed —
extraction runs when they are concretized. ``bean=True`` applies the
``@Repository`` stereotype so the subclass registers/scans like any
other component; ``@Component``/``@Repository`` decoration works equally.

Constructor injection: the repository takes the starter's transaction
manager (``SqlAlchemyTransactionManager`` /
``AsyncSqlAlchemyTransactionManager``) — a plain ``sessionmaker`` /
``async_sessionmaker`` works at runtime too (``current_session`` accepts
both) but the annotated injection contract is the manager. Every
operation resolves its session via ``current_session(..., require_tx=
True)``: repository methods *join the ambient transaction* and a call
outside one propagates ``NoTransactionError`` — the Spring Data semantic
that repository methods participate in the caller's transaction, never
silently open their own.

``save`` uses ``Session.merge`` (not ``add``/persist): merge copies
entity state onto a managed instance — SELECT+INSERT for transient,
SELECT+UPDATE for detached — and returns *that* instance, matching
Spring Data JPA ``save()``. Use the returned object; the argument is not
reattached. ``delete`` does ``session.delete`` + ``flush`` so the DELETE
reaches the database inside the ambient transaction and a rollback
undoes it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    TypeVar,
    cast,
    get_args,
    get_origin,
    overload,
)

from pyleaf.core import Repository as _RepositoryStereotype
from pyleaf.starter.sqlalchemy.tx import (
    AsyncSqlAlchemyTransactionManager,
    SqlAlchemyTransactionManager,
    current_async_session,
    current_session,
)
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, sessionmaker

from .paging import Page, Pageable, Sort

if TYPE_CHECKING:
    # ``sqlalchemy.ext.asyncio`` raises ImportError without greenlet;
    # the async repository only needs these names as annotations.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

T = TypeVar("T")
ID = TypeVar("ID")


class _RepositoryBase:
    """Shared ``__init_subclass__``: model extraction + ``bean=`` marker."""

    _model: ClassVar[type[Any]]

    def __init_subclass__(cls, *, bean: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__parameters__", ()):
            # Still generic — CrudRepository/AsyncCrudRepository itself or
            # an app-level generic intermediate. Extraction runs on the
            # concrete parametrized subclass instead.
            return
        model: Any = None
        for base in getattr(cls, "__orig_bases__", ()):
            origin = get_origin(base)
            if origin is CrudRepository or origin is AsyncCrudRepository:
                args = get_args(base)
                if len(args) != 2:
                    raise TypeError(
                        f"{cls.__name__} must parametrize its repository base"
                        f" as {origin.__name__}[T, ID], got {base}"
                    )
                model = args[0]
                break
        if model is not None:
            if not isinstance(model, type):
                raise TypeError(
                    f"{cls.__name__}: repository model must be a concrete"
                    f" mapped class, got {model!r}"
                )
            cls._model = model
        elif getattr(cls, "_model", None) is None:
            # A plain subclass of a concrete repo inherits ``_model``;
            # only a base with nothing resolvable is an error.
            raise TypeError(
                f"{cls.__name__} must subclass CrudRepository[T, ID] or"
                " AsyncCrudRepository[T, ID] with concrete type arguments"
            )
        if bean:
            _RepositoryStereotype(cls)


class CrudRepository(_RepositoryBase, Generic[T, ID]):
    """Synchronous typed repository over the ambient transaction's Session.

    All methods resolve ``current_session(self._source, require_tx=True)``
    — they join the caller's transaction and raise ``NoTransactionError``
    when none is active.
    """

    _source: sessionmaker[Session] | SqlAlchemyTransactionManager

    def __init__(self, tx_manager: SqlAlchemyTransactionManager) -> None:
        self._source = tx_manager

    def _session(self) -> Session:
        """The session bound to the ambient transaction."""
        return current_session(self._source)

    def save(self, entity: T) -> T:
        """Merge ``entity`` (insert-or-update); returns the managed instance."""
        return self._session().merge(entity)

    def save_all(self, entities: Iterable[T]) -> list[T]:
        """Merge every entity; returns the managed instances, in order."""
        session = self._session()
        return [session.merge(entity) for entity in entities]

    def find_by_id(self, ident: ID) -> T | None:
        """``Session.get`` — the entity or ``None``."""
        return self._session().get(self._model, cast(Any, ident))

    def exists_by_id(self, ident: ID) -> bool:
        """Whether a row with primary key ``ident`` exists."""
        pk = sa_inspect(self._model).primary_key[0]
        stmt = (
            select(func.count())
            .select_from(self._model)
            .where(pk == cast(Any, ident))
        )
        return int(self._session().scalar(stmt) or 0) > 0

    def count(self) -> int:
        """Row count of the mapped table."""
        stmt = select(func.count()).select_from(self._model)
        return int(self._session().scalar(stmt) or 0)

    @overload
    def find_all(self, *, sort: Sort | None = None) -> list[T]: ...

    @overload
    def find_all(
        self, *, pageable: Pageable, sort: Sort | None = None
    ) -> Page[T]: ...

    def find_all(
        self,
        *,
        sort: Sort | None = None,
        pageable: Pageable | None = None,
    ) -> list[T] | Page[T]:
        """All entities; sorted and/or paged per the arguments.

        ``sort`` and ``pageable.sort`` both apply (``sort`` first). With a
        ``pageable`` a ``count(*)`` runs alongside the page query and the
        result is a :class:`Page`; otherwise a plain ``list``.
        """
        stmt = select(self._model)
        orders: list[Any] = []
        if sort is not None:
            orders.extend(sort.to_clauses(self._model))
        if pageable is not None:
            orders.extend(pageable.sort.to_clauses(self._model))
        if orders:
            stmt = stmt.order_by(*orders)
        session = self._session()
        if pageable is None:
            return list(session.scalars(stmt).all())
        result = session.scalars(
            stmt.offset(pageable.offset).limit(pageable.limit)
        )
        return Page(
            content=list(result.all()),
            number=pageable.page,
            size=pageable.size,
            total_elements=self.count(),
        )

    def delete(self, entity: T) -> None:
        """Delete ``entity``; flushes so the DELETE is issued in this tx."""
        session = self._session()
        session.delete(entity)
        session.flush()

    def delete_by_id(self, ident: ID) -> bool:
        """Delete the row with primary key ``ident``; False when absent."""
        entity = self.find_by_id(ident)
        if entity is None:
            return False
        self.delete(entity)
        return True


class AsyncCrudRepository(_RepositoryBase, Generic[T, ID]):
    """Async mirror of :class:`CrudRepository` over ``AsyncSession``.

    Session resolution goes through ``current_async_session``;
    ``NoTransactionError`` propagates outside an ambient transaction just
    like the sync repository.
    """

    _source: async_sessionmaker[AsyncSession] | AsyncSqlAlchemyTransactionManager

    def __init__(self, tx_manager: AsyncSqlAlchemyTransactionManager) -> None:
        self._source = tx_manager

    async def _session(self) -> AsyncSession:
        """The async session bound to the ambient transaction."""
        return await current_async_session(self._source)

    async def save(self, entity: T) -> T:
        """Merge ``entity`` (insert-or-update); returns the managed instance."""
        return await (await self._session()).merge(entity)

    async def save_all(self, entities: Iterable[T]) -> list[T]:
        """Merge every entity; returns the managed instances, in order."""
        session = await self._session()
        return [await session.merge(entity) for entity in entities]

    async def find_by_id(self, ident: ID) -> T | None:
        """``AsyncSession.get`` — the entity or ``None``."""
        return await (await self._session()).get(self._model, cast(Any, ident))

    async def exists_by_id(self, ident: ID) -> bool:
        """Whether a row with primary key ``ident`` exists."""
        pk = sa_inspect(self._model).primary_key[0]
        stmt = (
            select(func.count())
            .select_from(self._model)
            .where(pk == cast(Any, ident))
        )
        return int(await (await self._session()).scalar(stmt) or 0) > 0

    async def count(self) -> int:
        """Row count of the mapped table."""
        stmt = select(func.count()).select_from(self._model)
        return int(await (await self._session()).scalar(stmt) or 0)

    @overload
    async def find_all(self, *, sort: Sort | None = None) -> list[T]: ...

    @overload
    async def find_all(
        self, *, pageable: Pageable, sort: Sort | None = None
    ) -> Page[T]: ...

    async def find_all(
        self,
        *,
        sort: Sort | None = None,
        pageable: Pageable | None = None,
    ) -> list[T] | Page[T]:
        """Async mirror of :meth:`CrudRepository.find_all`."""
        stmt = select(self._model)
        orders: list[Any] = []
        if sort is not None:
            orders.extend(sort.to_clauses(self._model))
        if pageable is not None:
            orders.extend(pageable.sort.to_clauses(self._model))
        if orders:
            stmt = stmt.order_by(*orders)
        session = await self._session()
        if pageable is None:
            result = await session.scalars(stmt)
            return list(result.all())
        result = await session.scalars(
            stmt.offset(pageable.offset).limit(pageable.limit)
        )
        return Page(
            content=list(result.all()),
            number=pageable.page,
            size=pageable.size,
            total_elements=await self.count(),
        )

    async def delete(self, entity: T) -> None:
        """Delete ``entity``; flushes so the DELETE is issued in this tx."""
        session = await self._session()
        await session.delete(entity)
        await session.flush()

    async def delete_by_id(self, ident: ID) -> bool:
        """Delete the row with primary key ``ident``; False when absent."""
        entity = await self.find_by_id(ident)
        if entity is None:
            return False
        await self.delete(entity)
        return True


__all__ = [
    "AsyncCrudRepository",
    "CrudRepository",
]
