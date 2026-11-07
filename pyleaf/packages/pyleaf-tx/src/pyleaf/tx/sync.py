"""Per-context transaction state, bound through contextvars.

``TransactionSynchronizationManager`` is the pyleaf analogue of Spring's
ThreadLocal-bound TransactionSynchronizationManager, built on a single
``contextvars.ContextVar`` holding an immutable snapshot. Semantics
(pinned by tests):

* **Copy-on-write snapshots.** Every mutation stores a fresh
  ``_TxContext`` — child contexts always see the state as of their
  creation, never the parent's later mutations.
* **asyncio tasks** snapshot the context at ``create_task`` time
  (copy-on-task-create): a task spawned AFTER ``init()`` sees the
  transaction; a task spawned BEFORE ``init()`` never sees bindings the
  parent makes afterwards.
* **Threads.** Each raw ``threading.Thread`` has its own top-level
  Context, so bindings do not leak across threads. Caveat: threads that
  run inside ONE shared ``contextvars.Context`` object (``ctx.run`` in
  several threads, as a naive pool might) share the same bindings —
  bind per-context, not per-thread.
* **Inactive state.** Readers are lenient (``get_resource`` → None,
  ``has_resource`` → False, ``get_synchronizations`` → (),
  metadata getters → defaults). Mutators — ``bind_resource``,
  ``unbind_resource``, ``register_synchronization``, the metadata
  setters — raise ``IllegalTransactionStateError``. ``init()`` raises if
  already active; ``clear()`` is an idempotent no-op when inactive.
* **suspend/resume.** ``suspend()`` fully deactivates the context and
  returns a ``SuspendedResources`` snapshot (None when inactive);
  ``resume(holder)`` installs the snapshot, replacing current state;
  ``resume(None)`` is a no-op. Nested suspend/resume pairs restore
  correctly: suspend → init+bind → suspend → resume(inner) → resume(outer).
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from .errors import IllegalTransactionStateError

if TYPE_CHECKING:
    from .definition import Isolation
    from .manager import TransactionSynchronization


@dataclass(frozen=True)
class _TxContext:
    """Immutable snapshot of the per-context transaction state."""

    resources: dict[Any, Any]
    synchronizations: tuple[TransactionSynchronization, ...]
    name: str | None
    read_only: bool
    isolation_level: Isolation | None


_current: ContextVar[_TxContext | None] = ContextVar(
    "pyleaf_tx_context", default=None
)


@dataclass(frozen=True)
class SuspendedResources:
    """Snapshot returned by ``suspend()``, consumed by ``resume()``."""

    resources: dict[Any, Any]
    synchronizations: tuple[TransactionSynchronization, ...]
    name: str | None
    read_only: bool
    isolation_level: Isolation | None


class TransactionSynchronizationManager:
    """Static API over the per-context transaction state.

    All methods are classmethods — the manager is never instantiated;
    state lives in the contextvar, not on the class.
    """

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def init(cls) -> None:
        """Initialize an empty transaction context in this context.

        Raises IllegalTransactionStateError if one is already active.
        """
        if cls.is_active():
            raise IllegalTransactionStateError(
                "A transaction context is already active in this context"
            )
        _current.set(
            _TxContext(
                resources={},
                synchronizations=(),
                name=None,
                read_only=False,
                isolation_level=None,
            )
        )

    @classmethod
    def clear(cls) -> None:
        """Discard the transaction context; a no-op when inactive."""
        _current.set(None)

    @classmethod
    def is_active(cls) -> bool:
        """Return True when a transaction context is initialized."""
        return _current.get() is not None

    @classmethod
    def _context(cls) -> _TxContext:
        ctx = _current.get()
        if ctx is None:
            raise IllegalTransactionStateError(
                "No transaction context is active in this context"
            )
        return ctx

    # -- resources -----------------------------------------------------------

    @classmethod
    def bind_resource(cls, key: Any, resource: Any) -> None:
        """Bind ``resource`` under ``key``; requires an active context."""
        ctx = cls._context()
        _current.set(replace(ctx, resources={**ctx.resources, key: resource}))

    @classmethod
    def unbind_resource(cls, key: Any) -> Any:
        """Remove and return the resource bound under ``key``.

        Requires an active context; raises KeyError when ``key`` is not bound.
        """
        ctx = cls._context()
        resource = ctx.resources[key]
        resources = dict(ctx.resources)
        del resources[key]
        _current.set(replace(ctx, resources=resources))
        return resource

    @classmethod
    def get_resource(cls, key: Any) -> Any:
        """Return the resource bound under ``key``, or None."""
        ctx = _current.get()
        return None if ctx is None else ctx.resources.get(key)

    @classmethod
    def has_resource(cls, key: Any) -> bool:
        """Return True when ``key`` has a bound resource."""
        ctx = _current.get()
        return ctx is not None and key in ctx.resources

    # -- synchronizations ------------------------------------------------------

    @classmethod
    def register_synchronization(cls, synchronization: TransactionSynchronization) -> None:
        """Append a synchronization; commit/rollback hooks run in this order.

        Requires an active context.
        """
        ctx = cls._context()
        _current.set(
            replace(ctx, synchronizations=(*ctx.synchronizations, synchronization))
        )

    @classmethod
    def get_synchronizations(cls) -> tuple[TransactionSynchronization, ...]:
        """Return the registered synchronizations in registration order."""
        ctx = _current.get()
        return () if ctx is None else ctx.synchronizations

    # -- suspend / resume -------------------------------------------------------

    @classmethod
    def suspend(cls) -> SuspendedResources | None:
        """Detach the current transaction state; None when inactive.

        After ``suspend`` the context is fully inactive — ``is_active()``
        is False and mutators raise until ``resume`` or a fresh ``init``.
        """
        ctx = _current.get()
        if ctx is None:
            return None
        _current.set(None)
        return SuspendedResources(
            resources=dict(ctx.resources),
            synchronizations=ctx.synchronizations,
            name=ctx.name,
            read_only=ctx.read_only,
            isolation_level=ctx.isolation_level,
        )

    @classmethod
    def resume(cls, holder: SuspendedResources | None) -> None:
        """Install a snapshot returned by ``suspend``; replaces current state.

        ``resume(None)`` is a no-op.
        """
        if holder is None:
            return
        _current.set(
            _TxContext(
                resources=dict(holder.resources),
                synchronizations=holder.synchronizations,
                name=holder.name,
                read_only=holder.read_only,
                isolation_level=holder.isolation_level,
            )
        )

    # -- current-transaction metadata -------------------------------------------

    @classmethod
    def set_current_transaction_name(cls, name: str | None) -> None:
        """Record the active transaction's name; requires an active context."""
        ctx = cls._context()
        _current.set(replace(ctx, name=name))

    @classmethod
    def current_transaction_name(cls) -> str | None:
        """Return the active transaction's name, or None."""
        ctx = _current.get()
        return None if ctx is None else ctx.name

    @classmethod
    def set_current_transaction_read_only(cls, read_only: bool) -> None:
        """Mark the active transaction read-only; requires an active context."""
        ctx = cls._context()
        _current.set(replace(ctx, read_only=read_only))

    @classmethod
    def is_current_transaction_read_only(cls) -> bool:
        """Return True when the active transaction is marked read-only."""
        ctx = _current.get()
        return ctx is not None and ctx.read_only

    @classmethod
    def set_current_transaction_isolation_level(
        cls, isolation_level: Isolation | None
    ) -> None:
        """Record the active transaction's isolation; requires an active context."""
        ctx = cls._context()
        _current.set(replace(ctx, isolation_level=isolation_level))

    @classmethod
    def current_transaction_isolation_level(cls) -> Isolation | None:
        """Return the active transaction's isolation level, or None."""
        ctx = _current.get()
        return None if ctx is None else ctx.isolation_level
