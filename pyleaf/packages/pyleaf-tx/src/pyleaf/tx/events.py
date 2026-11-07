"""``@TransactionalEventListener``: event delivery bound to tx phases (WI 3.3).

The marker is a strict superset of ``@EventListener``: it sets the core
``__pyleaf_eventlistener__`` attribute so the context's listener table
registers the method for its declared event type, plus
``__pyleaf_tx_event__ = (phase, fallback_execution)`` for the tx layer.

Two pieces implement the deferral:

* :class:`TxEventListenerPostProcessor` — a ``BeanPostProcessor`` bean
  provided by ``TxAutoConfiguration``. For each bean method carrying
  ``__pyleaf_tx_event__`` it wraps the *bound* method on the instance, so
  the context's dispatch (``getattr(bean, method_name)``) calls the
  wrapper transparently. A call inside a transaction appends
  ``(event, phase, bound_method)`` to the per-transaction
  :class:`TxEventSynchronization` — registered once per transaction on
  first use, so the same object accumulates events for every listener.
  Outside a transaction, ``fallback_execution=True`` invokes immediately;
  ``False`` drops the event with a debug log naming the event type.
* :class:`TxEventSynchronization` — the ``TransactionSynchronization``
  the boundary executor drives: ``before_commit`` delivers BEFORE_COMMIT
  events in order and *propagates* listener exceptions (rolling the
  transaction back — Spring semantics); ``after_commit`` delivers
  AFTER_COMMIT; ``after_completion`` delivers AFTER_ROLLBACK when the
  outcome is ROLLED_BACK and AFTER_COMPLETION always — both logging
  listener failures instead of raising. Each event is delivered exactly
  once per its phase.

Listeners must be synchronous functions: phase delivery happens inside
the synchronous boundary hooks of ``_SyncBoundary``/``_AsyncBoundary``,
so a coroutine listener is rejected with ``TypeError`` at decoration
time (async listeners cannot be awaited from a sync hook).
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from enum import Enum, auto
from functools import wraps
from typing import Any, TypeVar, overload

from pyleaf.core import BeanPostProcessor
from pyleaf.core.events import EVENT_LISTENER_ATTR, iter_event_listeners

from .manager import CompletionStatus, TransactionSynchronization
from .sync import TransactionSynchronizationManager as TSM

F = TypeVar("F", bound=Callable[..., Any])

_logger = logging.getLogger(__name__)

#: Attribute under which the marker stores ``(phase, fallback_execution)``.
TX_EVENT_ATTR = "__pyleaf_tx_event__"


class TxPhase(Enum):
    """When a ``@TransactionalEventListener`` fires relative to the commit."""

    BEFORE_COMMIT = auto()
    AFTER_COMMIT = auto()
    AFTER_ROLLBACK = auto()
    AFTER_COMPLETION = auto()


@overload
def TransactionalEventListener(target: F, /) -> F: ...


@overload
def TransactionalEventListener(
    target: None = None,
    /,
    *,
    phase: TxPhase = TxPhase.AFTER_COMMIT,
    fallback_execution: bool = True,
) -> Callable[[F], F]: ...


def TransactionalEventListener(
    target: Any = None,
    /,
    *,
    phase: TxPhase = TxPhase.AFTER_COMMIT,
    fallback_execution: bool = True,
) -> Any:
    """Mark a bean method as a transaction-phase event listener.

    Usable bare (``@TransactionalEventListener``) or with arguments. The
    method's single non-self parameter annotation declares the event type
    exactly like ``@EventListener``. ``phase`` selects the boundary hook
    that delivers the event; ``fallback_execution`` decides what happens
    when the event is published with no active transaction — ``True``
    invokes the listener immediately, ``False`` drops it with a debug log.

    Coroutine functions are rejected with ``TypeError`` at decoration:
    phase delivery runs inside synchronous synchronization hooks.
    """

    def wrap(func: Any) -> Any:
        if inspect.iscoroutinefunction(func):
            name = getattr(func, "__qualname__", repr(func))
            raise TypeError(
                f"@TransactionalEventListener {name}: coroutine functions"
                " are not supported — phase delivery happens inside"
                " synchronous boundary hooks"
            )
        setattr(func, EVENT_LISTENER_ATTR, True)
        setattr(func, TX_EVENT_ATTR, (phase, fallback_execution))
        return func

    if target is None:
        return wrap
    return wrap(target)


def _current_synchronization() -> TxEventSynchronization:
    """Return the tx's event accumulator, registering it on first use.

    One ``TxEventSynchronization`` per transaction accumulates the events
    of every deferred listener call.
    """
    for sync in TSM.get_synchronizations():
        if isinstance(sync, TxEventSynchronization):
            return sync
    sync = TxEventSynchronization()
    TSM.register_synchronization(sync)
    return sync


def _deferred_listener(
    bound: Callable[..., Any],
    phase: TxPhase,
    fallback_execution: bool,
    bean_name: str,
    method_name: str,
) -> Callable[..., Any]:
    """Wrap a bound listener method with the defer-or-deliver decision."""

    @wraps(bound)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        event = args[0] if args else next(iter(kwargs.values()), None)
        if TSM.is_active():
            _current_synchronization().enqueue(event, phase, bound)
            return None
        if fallback_execution:
            return bound(*args, **kwargs)
        _logger.debug(
            "Dropping %s event for @TransactionalEventListener %s.%s:"
            " no active transaction and fallback_execution=False",
            type(event).__name__,
            bean_name,
            method_name,
        )
        return None

    return wrapped


class TxEventListenerPostProcessor(BeanPostProcessor):
    """Wrap each ``@TransactionalEventListener`` bound method for deferral.

    Discovered as a regular post-processor bean (provided by
    ``TxAutoConfiguration``); because listener dispatch resolves the
    method via ``getattr`` on the bean instance, the instance-level
    wrapper set here intercepts every published event transparently.
    """

    def post_process_before_init(self, bean: object, name: str) -> object:
        """Pass-through — the Protocol's inherited ``...`` default would
        otherwise replace the bean with ``None``."""
        return bean

    def post_process_after_init(self, bean: object, name: str) -> object:
        for method_name, func in iter_event_listeners(type(bean)):
            meta = getattr(func, TX_EVENT_ATTR, None)
            if meta is None:
                continue
            phase, fallback_execution = meta
            bound: Callable[..., Any] = getattr(bean, method_name)
            setattr(
                bean,
                method_name,
                _deferred_listener(
                    bound, phase, fallback_execution, name, method_name
                ),
            )
        return bean


class TxEventSynchronization(TransactionSynchronization):
    """Per-transaction queue of deferred events, drained per phase.

    Registered once per transaction by the deferred-listener wrapper; the
    boundary executor invokes the hooks like any other synchronization.
    Each queued ``(event, phase, listener)`` is delivered exactly once,
    in enqueue order within its phase.
    """

    def __init__(self) -> None:
        self._pending: list[tuple[Any, TxPhase, Callable[..., Any]]] = []

    def enqueue(
        self, event: Any, phase: TxPhase, listener: Callable[..., Any]
    ) -> None:
        """Queue ``(event, phase, listener)`` for delivery at ``phase``."""
        self._pending.append((event, phase, listener))

    def before_commit(self) -> None:
        """Deliver BEFORE_COMMIT events; listener exceptions propagate
        (which rolls the transaction back — Spring semantics)."""
        self._deliver(TxPhase.BEFORE_COMMIT, propagate=True)

    def after_commit(self) -> None:
        """Deliver AFTER_COMMIT events; listener failures are logged."""
        self._deliver(TxPhase.AFTER_COMMIT, propagate=False)

    def after_completion(self, status: CompletionStatus) -> None:
        """Deliver AFTER_ROLLBACK on ROLLED_BACK, then AFTER_COMPLETION
        always; listener failures are logged."""
        if status is CompletionStatus.ROLLED_BACK:
            self._deliver(TxPhase.AFTER_ROLLBACK, propagate=False)
        self._deliver(TxPhase.AFTER_COMPLETION, propagate=False)

    def _deliver(self, phase: TxPhase, *, propagate: bool) -> None:
        """Drain and invoke every listener queued for ``phase``, in order."""
        ready = [entry for entry in self._pending if entry[1] is phase]
        self._pending = [
            entry for entry in self._pending if entry[1] is not phase
        ]
        for event, _, listener in ready:
            try:
                listener(event)
            except Exception:
                if propagate:
                    raise
                _logger.exception(
                    "@TransactionalEventListener %s failed handling %r",
                    getattr(listener, "__qualname__", listener),
                    event,
                )


__all__ = [
    "TX_EVENT_ATTR",
    "TransactionalEventListener",
    "TxEventListenerPostProcessor",
    "TxEventSynchronization",
    "TxPhase",
]
