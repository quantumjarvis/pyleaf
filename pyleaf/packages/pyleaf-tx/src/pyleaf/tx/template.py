"""The transaction executor shared by the interceptor and the template.

``_SyncBoundary`` / ``_AsyncBoundary`` implement the full propagation
state machine for one transactional boundary invocation; the
``@Transactional`` advice and :class:`TransactionTemplate` are thin
callers over them, which is what makes ``template.execute(fn)``
semantically identical to a ``@Transactional(REQUIRED)`` method.

Boundary shape (sync shown; async mirrors it with awaits):

* ``enter()`` computes a :class:`_Plan` from the propagation and the TSM
  state — early raises for MANDATORY/NEVER, ``TSM.suspend`` for
  NOT_SUPPORTED/REQUIRES_NEW, ``TSM.init`` for fresh transactions — then
  calls ``manager.begin`` and sets the current-tx metadata on the
  boundary that initialized the context.
* ``exit(exc)`` completes the boundary: only the outermost (TSM-inited)
  boundary drives synchronizations + commit/rollback; a NESTED
  (savepoint) status completes through the manager without
  synchronizations; a joined status only marks ``set_rollback_only``.
  ``TSM.clear``/``resume`` always run in ``finally``.
* ``run``/``run_async`` wrap a callable in enter/exit.

Manager shape validation: a *coroutine* ``begin`` can never serve a sync
boundary (rejected before any call); an async boundary accepts either a
coroutine ``begin`` or a sync ``begin`` returning an awaitable status —
a sync-only manager's non-awaitable ``begin`` result raises a clear
:class:`TransactionError` inside ``enter`` (and the boundary cleans up).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar, overload

from .definition import Isolation, Propagation, TransactionDefinition
from .errors import (
    IllegalTransactionStateError,
    NoTransactionError,
    TransactionError,
)
from .manager import CompletionStatus, TransactionSynchronization
from .status import TransactionStatus
from .sync import SuspendedResources
from .sync import TransactionSynchronizationManager as TSM

T = TypeVar("T")


@dataclass(frozen=True)
class _Plan:
    """How one boundary enters, derived from propagation + TSM state."""

    suspend: bool
    init: bool
    begin: bool


def _plan(propagation: Propagation, active: bool, owner: str) -> _Plan:
    """Decide the entry steps for ``propagation``; may raise up front."""
    if propagation is Propagation.NEVER:
        if active:
            raise IllegalTransactionStateError(
                f"{owner}: Propagation.NEVER violation —"
                " an existing transaction was found"
            )
        return _Plan(suspend=False, init=False, begin=False)
    if propagation is Propagation.MANDATORY and not active:
        raise NoTransactionError(
            f"{owner}: Propagation.MANDATORY violation —"
            " no existing transaction found"
        )
    if propagation is Propagation.NOT_SUPPORTED:
        return _Plan(suspend=active, init=False, begin=False)
    if propagation is Propagation.SUPPORTS and not active:
        return _Plan(suspend=False, init=False, begin=False)
    suspend = propagation is Propagation.REQUIRES_NEW and active
    init = suspend or not active
    return _Plan(suspend=suspend, init=init, begin=True)


def _before_commit(sync: TransactionSynchronization) -> None:
    hook = getattr(sync, "before_commit", None)
    if callable(hook):
        hook()


def _after_commit(sync: TransactionSynchronization) -> None:
    hook = getattr(sync, "after_commit", None)
    if callable(hook):
        hook()


def _after_completion(
    sync: TransactionSynchronization, status: CompletionStatus
) -> None:
    hook = getattr(sync, "after_completion", None)
    if callable(hook):
        hook(status)


class _SyncBoundary:
    """One synchronous transactional boundary: enter / exit / run."""

    __slots__ = ("_defn", "_inited", "_manager", "_name", "_status", "_suspended")

    def __init__(
        self,
        manager: Any,
        definition: TransactionDefinition,
        name: str,
    ) -> None:
        self._manager = manager
        self._defn = definition
        self._name = name
        self._status: TransactionStatus | None = None
        self._suspended: SuspendedResources | None = None
        self._inited = False

    @property
    def status(self) -> TransactionStatus | None:
        """The status begun by :meth:`enter`; None for untransacted paths."""
        return self._status

    def enter(self) -> None:
        """Open the boundary per the propagation plan; raises on violations."""
        plan = _plan(self._defn.propagation, TSM.is_active(), self._name)
        if plan.suspend:
            self._suspended = TSM.suspend()
        if plan.init:
            TSM.init()
            self._inited = True
        if plan.begin:
            try:
                self._status = self._begin()
            except BaseException:
                self._cleanup()
                raise
        if self._inited:
            TSM.set_current_transaction_name(self._name)
            TSM.set_current_transaction_read_only(self._defn.read_only)
            if self._defn.isolation is not Isolation.DEFAULT:
                TSM.set_current_transaction_isolation_level(self._defn.isolation)

    def _begin(self) -> TransactionStatus:
        result = self._manager.begin(self._defn)
        if inspect.iscoroutine(result):
            # An async-only manager slipped through (e.g. named bean).
            result.close()
            raise TransactionError(
                f"{self._name}: resolved manager {self._manager!r} is"
                " async-only — a synchronous method requires a"
                " TransactionManager"
            )
        status: TransactionStatus = result
        return status

    def _cleanup(self) -> None:
        if self._inited:
            TSM.clear()
            self._inited = False
        if self._suspended is not None:
            TSM.resume(self._suspended)
            self._suspended = None

    def exit(self, exc: BaseException | None) -> None:
        """Complete the boundary for outcome ``exc`` (None = success)."""
        status = self._status
        try:
            if status is not None:
                if self._inited:
                    if exc is not None and self._defn.rolls_back_on(exc):
                        self._rollback_outer(status)
                    else:
                        self._commit_outer(status)
                elif status.has_savepoint:
                    if exc is not None and self._defn.rolls_back_on(exc):
                        self._manager.rollback(status)
                    else:
                        self._manager.commit(status)
                elif exc is not None and self._defn.rolls_back_on(exc):
                    status.set_rollback_only()
        finally:
            self._cleanup()

    def _commit_outer(self, status: TransactionStatus) -> None:
        syncs = TSM.get_synchronizations()
        try:
            for sync in syncs:
                _before_commit(sync)
            self._manager.commit(status)
        except BaseException:
            for sync in syncs:
                _after_completion(sync, CompletionStatus.ROLLED_BACK)
            raise
        for sync in syncs:
            _after_commit(sync)
        for sync in syncs:
            _after_completion(sync, CompletionStatus.COMMITTED)

    def _rollback_outer(self, status: TransactionStatus) -> None:
        self._manager.rollback(status)
        for sync in TSM.get_synchronizations():
            _after_completion(sync, CompletionStatus.ROLLED_BACK)

    def run(self, body: Callable[[], T]) -> T:
        """Enter the boundary, run ``body``, complete per its outcome."""
        self.enter()
        try:
            result = body()
        except BaseException as exc:
            self.exit(exc)
            raise
        self.exit(None)
        return result


class _AsyncBoundary:
    """The async mirror of :class:`_SyncBoundary`."""

    __slots__ = ("_defn", "_inited", "_manager", "_name", "_status", "_suspended")

    def __init__(
        self,
        manager: Any,
        definition: TransactionDefinition,
        name: str,
    ) -> None:
        self._manager = manager
        self._defn = definition
        self._name = name
        self._status: TransactionStatus | None = None
        self._suspended: SuspendedResources | None = None
        self._inited = False

    @property
    def status(self) -> TransactionStatus | None:
        """The status begun by :meth:`enter`; None for untransacted paths."""
        return self._status

    async def enter(self) -> None:
        plan = _plan(self._defn.propagation, TSM.is_active(), self._name)
        if plan.suspend:
            self._suspended = TSM.suspend()
        if plan.init:
            TSM.init()
            self._inited = True
        if plan.begin:
            try:
                self._status = await self._begin()
            except BaseException:
                self._cleanup()
                raise
        if self._inited:
            TSM.set_current_transaction_name(self._name)
            TSM.set_current_transaction_read_only(self._defn.read_only)
            if self._defn.isolation is not Isolation.DEFAULT:
                TSM.set_current_transaction_isolation_level(self._defn.isolation)

    async def _begin(self) -> TransactionStatus:
        result = self._manager.begin(self._defn)
        if inspect.isawaitable(result):
            # Coroutine begin (async manager) or awaitable status
            # (dual-capable manager).
            status: TransactionStatus = await result
            return status
        raise TransactionError(
            f"{self._name}: resolved manager {self._manager!r} is"
            " synchronous — an async method requires an"
            " AsyncTransactionManager"
        )

    def _cleanup(self) -> None:
        if self._inited:
            TSM.clear()
            self._inited = False
        if self._suspended is not None:
            TSM.resume(self._suspended)
            self._suspended = None

    async def _maybe_await(self, result: Any) -> Any:
        if inspect.isawaitable(result):
            return await result
        return result

    async def exit(self, exc: BaseException | None) -> None:
        status = self._status
        try:
            if status is not None:
                if self._inited:
                    if exc is not None and self._defn.rolls_back_on(exc):
                        await self._rollback_outer(status)
                    else:
                        await self._commit_outer(status)
                elif status.has_savepoint:
                    if exc is not None and self._defn.rolls_back_on(exc):
                        await self._maybe_await(self._manager.rollback(status))
                    else:
                        await self._maybe_await(self._manager.commit(status))
                elif exc is not None and self._defn.rolls_back_on(exc):
                    status.set_rollback_only()
        finally:
            self._cleanup()

    async def _commit_outer(self, status: TransactionStatus) -> None:
        syncs = TSM.get_synchronizations()
        try:
            for sync in syncs:
                _before_commit(sync)
            await self._maybe_await(self._manager.commit(status))
        except BaseException:
            for sync in syncs:
                _after_completion(sync, CompletionStatus.ROLLED_BACK)
            raise
        for sync in syncs:
            _after_commit(sync)
        for sync in syncs:
            _after_completion(sync, CompletionStatus.COMMITTED)

    async def _rollback_outer(self, status: TransactionStatus) -> None:
        await self._maybe_await(self._manager.rollback(status))
        for sync in TSM.get_synchronizations():
            _after_completion(sync, CompletionStatus.ROLLED_BACK)

    async def run(self, body: Callable[[], Awaitable[T]]) -> T:
        """Enter the boundary, await ``body``, complete per its outcome."""
        await self.enter()
        try:
            result = await body()
        except BaseException as exc:
            await self.exit(exc)
            raise
        await self.exit(None)
        return result


class _TemplateTransaction(Generic[T]):
    """The object returned by ``TransactionTemplate.required()``.

    Implements both the sync and async context-manager protocols; the
    async entry asserts an async-capable manager (the boundary's
    awaitable-``begin`` check) and the sync entry rejects a coroutine
    ``begin`` up front.
    """

    __slots__ = ("_boundary", "_definition", "_manager")

    def __init__(
        self, manager: Any, definition: TransactionDefinition
    ) -> None:
        self._manager = manager
        self._definition = definition
        self._boundary: _SyncBoundary | _AsyncBoundary | None = None

    def __enter__(self) -> TransactionStatus | None:
        begin = getattr(self._manager, "begin", None)
        if not callable(begin) or inspect.iscoroutinefunction(begin):
            raise TransactionError(
                "TransactionTemplate.required(): a synchronous 'with'"
                " block requires a TransactionManager (sync"
                " begin/commit/rollback)"
            )
        boundary = _SyncBoundary(
            self._manager, self._definition, "TransactionTemplate.required"
        )
        boundary.enter()
        self._boundary = boundary
        return boundary.status

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> Literal[False]:
        boundary = self._boundary
        if isinstance(boundary, _SyncBoundary):
            boundary.exit(exc)
        return False

    async def __aenter__(self) -> TransactionStatus | None:
        begin = getattr(self._manager, "begin", None)
        if not callable(begin):
            raise TransactionError(
                "TransactionTemplate.required(): an 'async with' block"
                " requires an AsyncTransactionManager"
            )
        boundary = _AsyncBoundary(
            self._manager, self._definition, "TransactionTemplate.required"
        )
        await boundary.enter()
        self._boundary = boundary
        return boundary.status

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> Literal[False]:
        boundary = self._boundary
        if isinstance(boundary, _AsyncBoundary):
            await boundary.exit(exc)
        return False


class TransactionTemplate:
    """Programmatic transactions — same state machine as ``@Transactional``.

    ``execute(fn)`` runs ``fn`` inside a boundary built from this
    template's definition (``REQUIRED`` by default) on the injected
    manager: coroutine functions take the async path (awaitable begin
    required), everything else the sync path (a coroutine ``begin`` is
    rejected). ``required()`` returns a boundary usable as a ``with`` or
    ``async with`` block.
    """

    def __init__(
        self,
        manager: Any,
        definition: TransactionDefinition | None = None,
    ) -> None:
        self._manager = manager
        self._definition = (
            definition if definition is not None else TransactionDefinition()
        )

    @property
    def manager(self) -> Any:
        """The transaction manager this template drives."""
        return self._manager

    @property
    def definition(self) -> TransactionDefinition:
        """The definition every boundary is built from."""
        return self._definition

    def with_definition(
        self, definition: TransactionDefinition
    ) -> TransactionTemplate:
        """Return a new template on the same manager with ``definition``."""
        return TransactionTemplate(self._manager, definition)

    def required(self) -> _TemplateTransaction[Any]:
        """A boundary usable as both ``with`` and ``async with``."""
        return _TemplateTransaction(self._manager, self._definition)

    @overload
    def execute(
        self, fn: Callable[[], Awaitable[T]]
    ) -> Awaitable[T]: ...

    @overload
    def execute(self, fn: Callable[[], T]) -> T: ...

    def execute(self, fn: Callable[[], Any]) -> Any:
        """Run ``fn`` inside this template's transaction boundary.

        Coroutine functions return a coroutine to await; generator and
        async-generator functions are rejected with ``TypeError``.
        """
        if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
            raise TypeError(
                "TransactionTemplate.execute: generator and"
                " async-generator functions cannot be transactional"
            )
        name = getattr(fn, "__qualname__", None) or "TransactionTemplate.execute"
        if inspect.iscoroutinefunction(fn):
            return self._execute_async(fn, name)
        begin = getattr(self._manager, "begin", None)
        if not callable(begin) or inspect.iscoroutinefunction(begin):
            raise TransactionError(
                f"TransactionTemplate.execute({name}): a synchronous"
                " function requires a TransactionManager (sync"
                " begin/commit/rollback)"
            )
        return _SyncBoundary(self._manager, self._definition, name).run(fn)

    async def _execute_async(
        self, fn: Callable[[], Awaitable[T]], name: str
    ) -> T:
        begin = getattr(self._manager, "begin", None)
        if not callable(begin):
            raise TransactionError(
                f"TransactionTemplate.execute({name}): an async function"
                " requires an AsyncTransactionManager"
            )
        return await _AsyncBoundary(self._manager, self._definition, name).run(fn)
