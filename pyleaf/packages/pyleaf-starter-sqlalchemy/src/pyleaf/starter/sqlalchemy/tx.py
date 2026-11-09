"""SQLAlchemy transaction managers (WI 3.4, PYL-35).

Location decision (design 3.4, option (a)): the managers live inside
``pyleaf-starter-sqlalchemy`` as ``pyleaf.starter.sqlalchemy.tx`` rather
than a separate package. The starter already owns the SQLAlchemy
dependency, and keeping both managers in this one module does not force
async drivers on sync users: ``sqlalchemy.ext.asyncio`` is imported
defensively (it raises ``ImportError`` when ``greenlet`` is absent) and
only :class:`AsyncSqlAlchemyTransactionManager` *construction* requires
it — the module itself always loads.

Ownership model: the transaction *boundary*
(:mod:`pyleaf.tx.template`) decides WHEN a transaction completes — it
drives suspend/resume for ``REQUIRES_NEW``/``NOT_SUPPORTED`` and invokes
``commit``/``rollback`` on the manager. The manager owns the session
lifecycle: for an ``is_new`` status, ``commit``/``rollback`` performs
``session.commit()``/``session.rollback()``, removes the TSM resource
binding and closes the session — ``session.close()`` returns the
connection to the pool, so a QueuePool's checked-out count returns to 0
(no per-tx ``TransactionSynchronization`` is registered by the manager;
user synchronizations are still driven by the outer boundary).
Joined (``is_new=False``, no savepoint) statuses are no-ops on
commit/rollback except that ``set_rollback_only`` marks the shared TSM
handle, so the outer commit sees it (a commit on a rollback-only
transaction rolls back, then raises :class:`UnexpectedRollbackError` —
Spring's semantics). ``NESTED`` statuses wrap a
``SessionTransaction``/``AsyncSessionTransaction`` savepoint: commit
releases the savepoint, rollback rolls back to it, and the session stays
open and bound.

Resource binding is keyed by the ``sessionmaker``/``async_sessionmaker``
instance (Spring keys by SessionFactory), so multiple datasources work:
each manager binds under its own factory and ``current_session``
resolves the session bound for the factory (or owning manager) it is
given.

Isolation/read-only/timeout handling:

* ``isolation != DEFAULT`` → on postgres, ``SET TRANSACTION ISOLATION
  LEVEL ...`` is emitted as the transaction's first statement (the
  ``isolation_level`` connection execution option is unusable here — it
  is a *transactional* characteristic that SA rejects once
  ``session.begin()`` has created the Transaction object); on
  mysql/mariadb a best-effort ``SET SESSION TRANSACTION ISOLATION
  LEVEL`` is emitted; other dialects (sqlite) ignore it — documented
  best effort.
* ``read_only=True`` → ``SET TRANSACTION ... READ ONLY`` on postgres
  (a dialect-name check, not try/except) plus a ``before_flush`` event
  listener on the session that raises :class:`TransactionError` — so
  writes are rejected on any dialect.
* ``timeout_s`` is stored on the status handle and, on postgres,
  enforced best-effort via ``SELECT set_config('statement_timeout', ...)``
  for the current transaction. No watchdog is built.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, overload

from pyleaf.tx import (
    Isolation,
    NoTransactionError,
    Propagation,
    TransactionDefinition,
    TransactionError,
    TransactionStatus,
    UnexpectedRollbackError,
)
from pyleaf.tx.sync import TransactionSynchronizationManager as TSM

from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
    )

# ``sqlalchemy.ext.asyncio`` raises ImportError at import when greenlet is
# missing; the async manager needs it, the sync one must not.
_sa_asyncio: Any
try:
    import sqlalchemy.ext.asyncio as _sa_asyncio
except ImportError:
    _sa_asyncio = None

#: ANSI isolation names for the pyleaf Isolation enum (DEFAULT excluded —
#: it never reaches the mapping).
_ISOLATION_LEVELS: dict[Isolation, str] = {
    Isolation.READ_UNCOMMITTED: "READ UNCOMMITTED",
    Isolation.READ_COMMITTED: "READ COMMITTED",
    Isolation.REPEATABLE_READ: "REPEATABLE READ",
    Isolation.SERIALIZABLE: "SERIALIZABLE",
}

_POSTGRES = "postgresql"

#: Dialects on which a non-default isolation is applied at all (best
#: effort — sqlite and friends silently ignore it, documented).
_ISOLATION_DIALECTS = frozenset({_POSTGRES, "mysql", "mariadb"})


@dataclass
class _SessionHandle:
    """The TSM-bound resource for one outer (non-savepoint) transaction.

    Bound under the sessionmaker key for the transaction's whole life —
    including across a joined boundary's ``set_rollback_only`` mark, which
    lands on ``rollback_only`` here so the outer commit sees it.
    """

    session: Any  # Session | AsyncSession
    rollback_only: bool = False
    flush_guard: Callable[..., Any] | None = None
    self_inited: bool = False  # manager (not a boundary) called TSM.init()
    # Recorded from the definition for observability; enforcement is the
    # statement_timeout applied in _apply_options (postgres only).
    timeout_s: float | None = None


@dataclass
class _BoundStatus(TransactionStatus):
    """A ``TransactionStatus`` linked to its TSM-bound ``_SessionHandle``."""

    handle: _SessionHandle | None = None

    def set_rollback_only(self) -> None:
        super().set_rollback_only()
        if self.handle is not None:
            self.handle.rollback_only = True

    def is_rollback_only(self) -> bool:
        return self.rollback_only or (
            self.handle is not None and self.handle.rollback_only
        )


def _install_flush_guard(session: Session) -> Callable[..., Any]:
    """Block any flush on a read-only transaction's session."""

    def _guard(_session: Session, _flush_context: Any, _instances: Any) -> None:
        raise TransactionError("read-only transaction: flush rejected")

    event.listen(session, "before_flush", _guard)
    return _guard


def _remove_flush_guard(session: Session, guard: Callable[..., Any]) -> None:
    if event.contains(session, "before_flush", guard):
        event.remove(session, "before_flush", guard)


class SqlAlchemyTransactionManager:
    """Synchronous transaction manager over ``sqlalchemy.orm.Session``.

    Implements :class:`pyleaf.tx.TransactionManager`: ``begin`` opens or
    joins a session per the propagation (``NESTED`` on a bound session
    opens a savepoint), ``commit``/``rollback`` complete it per the
    ownership model in the module docstring.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """The sessionmaker this manager binds transactions under."""
        return self._session_factory

    def begin(self, definition: TransactionDefinition) -> TransactionStatus:
        """Open or join a session transaction per ``definition.propagation``.

        ``REQUIRES_NEW`` suspension is the boundary's job — called after a
        suspend this method sees no bound session and simply opens a new
        one, sharing the REQUIRED-new path.
        """
        handle = TSM.get_resource(self._session_factory)
        if handle is not None:
            session: Session = handle.session
            if definition.propagation is Propagation.NESTED:
                return _BoundStatus(
                    transaction=session.begin_nested(),
                    is_new=False,
                    has_savepoint=True,
                    handle=handle,
                )
            return _BoundStatus(
                transaction=session, is_new=False, handle=handle
            )
        inited = not TSM.is_active()
        if inited:
            # Standalone use (no boundary): the caller's context must be
            # initialized before bind_resource; _release clears it again.
            TSM.init()
        session = self._session_factory()
        try:
            session.begin()
            self._apply_options(session, definition)
        except BaseException:
            session.close()
            if inited:
                TSM.clear()
            raise
        handle = _SessionHandle(
            session=session,
            self_inited=inited,
            timeout_s=definition.timeout_s,
        )
        if definition.read_only:
            handle.flush_guard = _install_flush_guard(session)
        TSM.bind_resource(self._session_factory, handle)
        return _BoundStatus(transaction=session, is_new=True, handle=handle)

    def _apply_options(
        self, session: Session, definition: TransactionDefinition
    ) -> None:
        """Apply isolation / read-only / timeout to the new transaction."""
        dialect = session.get_bind().dialect.name
        isolation = _ISOLATION_LEVELS.get(definition.isolation)
        if dialect == _POSTGRES:
            clauses: list[str] = []
            if isolation is not None:
                clauses.append(f"ISOLATION LEVEL {isolation}")
            if definition.read_only:
                clauses.append("READ ONLY")
            if clauses:
                # Clause values come from _ISOLATION_LEVELS/literals only.
                stmt = "SET TRANSACTION " + ", ".join(clauses)
                session.execute(text(stmt))
            if definition.timeout_s is not None:
                millis = max(1, int(definition.timeout_s * 1000))
                session.execute(
                    text("SELECT set_config('statement_timeout', :ms, true)"),
                    {"ms": str(millis)},
                )
        elif isolation is not None and dialect in _ISOLATION_DIALECTS:
            stmt = f"SET SESSION TRANSACTION ISOLATION LEVEL {isolation}"
            session.execute(text(stmt))

    def commit(self, status: TransactionStatus) -> None:
        """Commit ``status``; rollback-only commits roll back then raise."""
        try:
            if status.is_new or status.has_savepoint:
                if status.is_rollback_only():
                    status.transaction.rollback()
                    raise UnexpectedRollbackError(
                        "transaction was marked rollback-only"
                    )
                status.transaction.commit()
            # Joined status: no-op — the outer boundary drives completion.
        finally:
            status.set_completed()
            if status.is_new and not status.has_savepoint:
                self._release(status)

    def rollback(self, status: TransactionStatus) -> None:
        """Roll back ``status`` (to the savepoint for NESTED)."""
        try:
            if status.is_new or status.has_savepoint:
                status.transaction.rollback()
        finally:
            status.set_completed()
            if status.is_new and not status.has_savepoint:
                self._release(status)

    def _release(self, status: TransactionStatus) -> None:
        """Unbind the TSM resource and close the session (pool check-in)."""
        session: Session = status.transaction
        handle = status.handle if isinstance(status, _BoundStatus) else None
        if handle is not None and handle.flush_guard is not None:
            _remove_flush_guard(session, handle.flush_guard)
            handle.flush_guard = None
        try:
            if TSM.is_active() and TSM.has_resource(self._session_factory):
                TSM.unbind_resource(self._session_factory)
        finally:
            session.close()
            if handle is not None and handle.self_inited and TSM.is_active():
                TSM.clear()


class AsyncSqlAlchemyTransactionManager:
    """Async transaction manager over ``sqlalchemy.ext.asyncio.AsyncSession``.

    Implements :class:`pyleaf.tx.AsyncTransactionManager`. Construction
    requires ``sqlalchemy.ext.asyncio`` (i.e. ``greenlet``); without it a
    clear :class:`TransactionError` is raised here rather than an
    ImportError at module import.
    """

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        if _sa_asyncio is None:
            raise TransactionError(
                "AsyncSqlAlchemyTransactionManager requires sqlalchemy's"
                " asyncio support — install 'greenlet' (or"
                " 'sqlalchemy[asyncio]') and an async driver"
            )
        self._session_factory = session_factory

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """The async_sessionmaker this manager binds transactions under."""
        return self._session_factory

    async def begin(self, definition: TransactionDefinition) -> TransactionStatus:
        """Async mirror of :meth:`SqlAlchemyTransactionManager.begin`."""
        handle = TSM.get_resource(self._session_factory)
        if handle is not None:
            session: AsyncSession = handle.session
            if definition.propagation is Propagation.NESTED:
                nested = session.begin_nested()
                await nested.start()
                return _BoundStatus(
                    transaction=nested,
                    is_new=False,
                    has_savepoint=True,
                    handle=handle,
                )
            return _BoundStatus(
                transaction=session, is_new=False, handle=handle
            )
        inited = not TSM.is_active()
        if inited:
            TSM.init()
        session = self._session_factory()
        try:
            await session.begin().start()
            await self._apply_options(session, definition)
        except BaseException:
            await session.close()
            if inited:
                TSM.clear()
            raise
        handle = _SessionHandle(
            session=session,
            self_inited=inited,
            timeout_s=definition.timeout_s,
        )
        if definition.read_only:
            handle.flush_guard = _install_flush_guard(session.sync_session)
        TSM.bind_resource(self._session_factory, handle)
        return _BoundStatus(transaction=session, is_new=True, handle=handle)

    async def _apply_options(
        self, session: AsyncSession, definition: TransactionDefinition
    ) -> None:
        """Apply isolation / read-only / timeout to the new transaction."""
        dialect = session.get_bind().dialect.name
        isolation = _ISOLATION_LEVELS.get(definition.isolation)
        if dialect == _POSTGRES:
            clauses: list[str] = []
            if isolation is not None:
                clauses.append(f"ISOLATION LEVEL {isolation}")
            if definition.read_only:
                clauses.append("READ ONLY")
            if clauses:
                stmt = "SET TRANSACTION " + ", ".join(clauses)
                await session.execute(text(stmt))
            if definition.timeout_s is not None:
                millis = max(1, int(definition.timeout_s * 1000))
                await session.execute(
                    text("SELECT set_config('statement_timeout', :ms, true)"),
                    {"ms": str(millis)},
                )
        elif isolation is not None and dialect in _ISOLATION_DIALECTS:
            stmt = f"SET SESSION TRANSACTION ISOLATION LEVEL {isolation}"
            await session.execute(text(stmt))

    async def commit(self, status: TransactionStatus) -> None:
        """Async mirror of the sync ``commit``."""
        try:
            if status.is_new or status.has_savepoint:
                if status.is_rollback_only():
                    await status.transaction.rollback()
                    raise UnexpectedRollbackError(
                        "transaction was marked rollback-only"
                    )
                await status.transaction.commit()
        finally:
            status.set_completed()
            if status.is_new and not status.has_savepoint:
                await self._release(status)

    async def rollback(self, status: TransactionStatus) -> None:
        """Async mirror of the sync ``rollback``."""
        try:
            if status.is_new or status.has_savepoint:
                await status.transaction.rollback()
        finally:
            status.set_completed()
            if status.is_new and not status.has_savepoint:
                await self._release(status)

    async def _release(self, status: TransactionStatus) -> None:
        """Unbind the TSM resource and close the session."""
        session: AsyncSession = status.transaction
        handle = status.handle if isinstance(status, _BoundStatus) else None
        if handle is not None and handle.flush_guard is not None:
            _remove_flush_guard(session.sync_session, handle.flush_guard)
            handle.flush_guard = None
        try:
            if TSM.is_active() and TSM.has_resource(self._session_factory):
                TSM.unbind_resource(self._session_factory)
        finally:
            await session.close()
            if handle is not None and handle.self_inited and TSM.is_active():
                TSM.clear()


def _resource_key(source: Any) -> Any:
    """Map a manager or session factory to its TSM resource key."""
    if isinstance(
        source,
        (SqlAlchemyTransactionManager, AsyncSqlAlchemyTransactionManager),
    ):
        return source.session_factory
    return source


@overload
def current_session(
    source: sessionmaker[Session] | SqlAlchemyTransactionManager,
    *,
    require_tx: Literal[True] = ...,
) -> Session: ...


@overload
def current_session(
    source: sessionmaker[Session] | SqlAlchemyTransactionManager,
    *,
    require_tx: bool,
) -> Session | None: ...


def current_session(
    source: sessionmaker[Session] | SqlAlchemyTransactionManager,
    *,
    require_tx: bool = True,
) -> Session | None:
    """Return the session bound to ``source``'s factory in the current tx.

    ``source`` may be the sessionmaker itself or the
    :class:`SqlAlchemyTransactionManager` owning it — both resolve to the
    same TSM resource key, so multi-datasource setups work per factory.
    With ``require_tx=True`` (default) an unbound call raises
    :class:`NoTransactionError`; with ``require_tx=False`` it returns
    ``None``.
    """
    handle = TSM.get_resource(_resource_key(source))
    if handle is None:
        if require_tx:
            raise NoTransactionError(
                "current_session: no session is bound for the given"
                " session factory — no transaction is active"
            )
        return None
    session: Session = handle.session if isinstance(handle, _SessionHandle) else handle
    return session


@overload
async def current_async_session(
    source: async_sessionmaker[AsyncSession] | AsyncSqlAlchemyTransactionManager,
    *,
    require_tx: Literal[True] = ...,
) -> AsyncSession: ...


@overload
async def current_async_session(
    source: async_sessionmaker[AsyncSession] | AsyncSqlAlchemyTransactionManager,
    *,
    require_tx: bool,
) -> AsyncSession | None: ...


async def current_async_session(
    source: async_sessionmaker[AsyncSession] | AsyncSqlAlchemyTransactionManager,
    *,
    require_tx: bool = True,
) -> AsyncSession | None:
    """Async mirror of :func:`current_session`."""
    handle = TSM.get_resource(_resource_key(source))
    if handle is None:
        if require_tx:
            raise NoTransactionError(
                "current_async_session: no session is bound for the given"
                " session factory — no transaction is active"
            )
        return None
    session: AsyncSession = (
        handle.session if isinstance(handle, _SessionHandle) else handle
    )
    return session


__all__ = [
    "AsyncSqlAlchemyTransactionManager",
    "SqlAlchemyTransactionManager",
    "current_async_session",
    "current_session",
]
