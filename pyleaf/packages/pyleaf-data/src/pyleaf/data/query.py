"""The ``@Query`` decorator — SQLAlchemy statement builders on repositories.

Statements are *built*, never parsed: ``stmt_fn`` must be a callable
``(model, *args, **kwargs) -> Select | Update | Delete | Insert`` — the
repository's bound model class is passed first, then the decorated
method's arguments. Strings are rejected by the API shape (``stmt_fn``
must be callable); there is no string-query path at all.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal, TypeVar, cast

from sqlalchemy import Select

#: The ``returns`` hint selects how a statement is executed:
#: ``"list"`` (default) → ``scalars(stmt).all()`` → ``list[T]``;
#: ``"one"`` → ``scalars(stmt).one_or_none()`` → ``T | None``;
#: ``"scalar"`` → ``execute(stmt).scalar()`` for ``Select``s,
#: ``execute(stmt).rowcount`` for DML statements.
ReturnHint = Literal["list", "one", "scalar"]

_VALID_RETURNS: frozenset[str] = frozenset({"list", "one", "scalar"})

F = TypeVar("F", bound=Callable[..., Any])


def Query(
    stmt_fn: Callable[..., Any],
    *,
    returns: ReturnHint = "list",
) -> Callable[[F], F]:
    """Attach a statement builder to a repository method.

    The decorated method's body is replaced: calling it invokes
    ``stmt_fn(model, *args, **kwargs)``, executes the returned statement
    on the ambient transaction's session and shapes the result per
    ``returns``. The method keeps its own signature — the annotation
    remains the declared result type for callers and type checkers.

    Works on both sync (``CrudRepository``) and async
    (``AsyncCrudRepository``) methods — a coroutine function gets an
    async wrapper awaiting the async session resolution and execution.
    """
    if not callable(stmt_fn):
        raise TypeError(
            "@Query expects a statement-builder callable"
            " (model, *args, **kwargs) -> Select|Update|Delete|Insert;"
            f" got {stmt_fn!r}"
        )
    if returns not in _VALID_RETURNS:
        raise ValueError(
            f"@Query returns= must be one of {sorted(_VALID_RETURNS)},"
            f" got {returns!r}"
        )

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_impl(self: Any, *args: Any, **kwargs: Any) -> Any:
                session = await self._session()
                stmt = stmt_fn(self._model, *args, **kwargs)
                if returns == "list":
                    result = await session.scalars(stmt)
                    return list(result.all())
                if returns == "one":
                    result = await session.scalars(stmt)
                    return result.one_or_none()
                result = await session.execute(stmt)
                return result.scalar() if isinstance(stmt, Select) else result.rowcount

            return cast(F, async_impl)

        @wraps(func)
        def sync_impl(self: Any, *args: Any, **kwargs: Any) -> Any:
            session = self._session()
            stmt = stmt_fn(self._model, *args, **kwargs)
            if returns == "list":
                return list(session.scalars(stmt).all())
            if returns == "one":
                return session.scalars(stmt).one_or_none()
            result = session.execute(stmt)
            return result.scalar() if isinstance(stmt, Select) else result.rowcount

        return cast(F, sync_impl)

    return decorator


__all__ = ["Query", "ReturnHint"]
