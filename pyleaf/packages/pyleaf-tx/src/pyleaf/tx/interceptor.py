"""``@Transactional`` interception on the M1 AOP chain.

:class:`TransactionInterceptor` is a regular ``@Aspect`` bean produced by
``TxAutoConfiguration`` (or registered by hand). It carries two ``@Around``
advices — one sync, one async — because the AOP matcher requires advice
and target to agree on coroutine-ness; each advice's pointcut is the
``@Transactional`` marker pointcut intersected with a sync/async method
filter.

* Marker matching is the single helper :func:`transactional_meta` — the
  pointcut predicate additionally rejects generator/async-generator
  functions with a ``TypeError`` naming the method, so an annotated
  generator fails at ``refresh()`` (the first match).
* The manager is resolved lazily per method and cached: ``manager="x"``
  looks the bean up by name, otherwise by protocol type —
  ``TransactionManager`` for sync methods, ``AsyncTransactionManager``
  for async ones (method shape decided by
  ``inspect.iscoroutinefunction``). A sync method resolving to a
  coroutine-``begin`` manager fails fast at first invocation with a
  ``TransactionError`` naming the method; the async-mismatch direction
  fails inside the boundary's ``enter`` (a sync ``begin`` result that is
  not awaitable). A context with no resolvable manager raises the same
  class of error at first invocation — the interceptor itself never
  blocks refresh.
* Advice order defaults to ``100`` (Spring's tx convention: outermost);
  override with the ``pyleaf.tx.order`` environment property, applied by
  ``TxAutoConfiguration``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Invocation,
    Order,
    Pointcut,
)
from pyleaf.core.errors import PyleafError

from .errors import TransactionError
from .manager import AsyncTransactionManager, TransactionManager
from .template import _AsyncBoundary, _SyncBoundary
from .transactional import TransactionalMeta, transactional_meta

#: Default order of the tx advice (Spring's convention: lowest precedence).
DEFAULT_TX_ORDER = 100

#: Environment property overriding the tx advice order.
TX_ORDER_PROPERTY = "pyleaf.tx.order"


def _tx_meta_match(
    bean_cls: type[Any], method_name: str, func: Callable[..., Any]
) -> bool:
    """Pointcut predicate: does this method carry ``@Transactional``?

    Raises ``TypeError`` naming the method for generator /
    async-generator targets — evaluated once per method at refresh.
    """
    if transactional_meta(bean_cls, func) is None:
        return False
    if inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func):
        raise TypeError(
            f"@Transactional {bean_cls.__name__}.{method_name}:"
            " generator functions cannot be transactional"
        )
    return True


_TRANSACTIONAL: Pointcut = Pointcut(_tx_meta_match, "@Transactional")
_IS_SYNC: Pointcut = Pointcut(
    lambda _c, _n, f: not asyncio.iscoroutinefunction(f), "sync-method"
)
_IS_ASYNC: Pointcut = Pointcut(
    lambda _c, _n, f: asyncio.iscoroutinefunction(f), "async-method"
)


def _meta_for(invocation: Invocation) -> TransactionalMeta | None:
    """Re-resolve the marker metadata for a live invocation."""
    cls = type(invocation.target)
    func = getattr(cls, invocation.method_name, None)
    if func is None or not callable(func):
        return None
    return transactional_meta(cls, func)


@Aspect
@Order(DEFAULT_TX_ORDER)
class TransactionInterceptor:
    """``@Around`` advice implementing the ``@Transactional`` state machine.

    The actual boundary logic lives in :mod:`pyleaf.tx.template` — the
    same executor ``TransactionTemplate`` uses — so decorator and
    template semantics are identical by construction.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx
        # Per-method resolved managers, validated at first invocation.
        self._managers: dict[tuple[type[Any], str], Any] = {}

    @Around(_TRANSACTIONAL & _IS_SYNC)
    def invoke(self, invocation: Invocation) -> Any:
        """Synchronous transactional boundary around the target call."""
        meta = _meta_for(invocation)
        if meta is None:
            return invocation.proceed()
        owner = f"{type(invocation.target).__name__}.{invocation.method_name}"
        manager = self._manager_for(
            meta, type(invocation.target), invocation.method_name, owner,
            is_async=False,
        )
        return _SyncBoundary(manager, meta.definition, owner).run(
            invocation.proceed
        )

    @Around(_TRANSACTIONAL & _IS_ASYNC)
    async def invoke_async(self, invocation: Invocation) -> Any:
        """Asynchronous transactional boundary around the target call."""
        meta = _meta_for(invocation)
        if meta is None:
            return await invocation.proceed()
        owner = f"{type(invocation.target).__name__}.{invocation.method_name}"
        manager = self._manager_for(
            meta, type(invocation.target), invocation.method_name, owner,
            is_async=True,
        )
        return await _AsyncBoundary(manager, meta.definition, owner).run(
            invocation.proceed
        )

    def _manager_for(
        self,
        meta: TransactionalMeta,
        bean_cls: type[Any],
        method_name: str,
        owner: str,
        *,
        is_async: bool,
    ) -> Any:
        key = (bean_cls, method_name)
        if key not in self._managers:
            self._managers[key] = self._resolve(meta, owner, is_async=is_async)
        return self._managers[key]

    def _resolve(self, meta: TransactionalMeta, owner: str, *, is_async: bool) -> Any:
        """Resolve + validate the manager; cache-worthy result or raise."""
        if meta.manager is not None:
            try:
                manager = self._ctx.get_bean(meta.manager)
            except PyleafError as exc:
                raise TransactionError(
                    f"{owner}: named transaction manager bean"
                    f" '{meta.manager}' is not resolvable: {exc}"
                ) from exc
        else:
            protocol = AsyncTransactionManager if is_async else TransactionManager
            try:
                manager = self._ctx.get(protocol)
            except PyleafError as exc:
                raise TransactionError(
                    f"{owner}: no {protocol.__name__} bean is resolvable"
                    f" ({exc})"
                ) from exc
        begin = getattr(manager, "begin", None)
        if not callable(begin):
            raise TransactionError(
                f"{owner}: resolved manager {manager!r} has no 'begin'"
                " method — it is not a transaction manager"
            )
        if not is_async and inspect.iscoroutinefunction(begin):
            raise TransactionError(
                f"{owner}: resolved manager {manager!r} is async-only"
                " (coroutine 'begin') — a synchronous method requires a"
                " TransactionManager"
            )
        return manager


__all__ = [
    "DEFAULT_TX_ORDER",
    "TX_ORDER_PROPERTY",
    "TransactionInterceptor",
]
