"""The ``@Transactional`` marker: annotation + metadata carrier.

``@Transactional`` may decorate a method or a whole class. Class-level
marks are the default for every public method; a method-level mark
overrides the class-level mark entirely (Spring semantics). The marker
records a :class:`TransactionalMeta` — a :class:`TransactionDefinition`
plus an optional manager bean name — under ``__pyleaf_transactional__``
on the decorated object and also joins the ``__pyleaf_markers__``
convention from :func:`pyleaf.core.aop.mark` so ``annotated()``-style
pointcuts can match it.

Resolution at advice time goes through :func:`transactional_meta`:
method attribute first, then the (possibly inherited) class attribute.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, overload

from pyleaf.core.aop import MARKERS_ATTR

from .definition import Isolation, Propagation, TransactionDefinition

F = TypeVar("F", bound=Callable[..., Any])

#: Attribute under which :func:`Transactional` stores its metadata.
TX_META_ATTR = "__pyleaf_transactional__"

#: Marker recorded in ``__pyleaf_markers__`` for ``annotated()`` interop.
TRANSACTIONAL_MARKER = "pyleaf.tx.transactional"


@dataclass(frozen=True)
class TransactionalMeta:
    """What ``@Transactional`` leaves on a method or class."""

    definition: TransactionDefinition
    manager: str | None = None


def transactional_meta(
    bean_cls: type[Any], func: Callable[..., Any] | None
) -> TransactionalMeta | None:
    """Resolve the effective ``@Transactional`` metadata for a method.

    Method-level metadata wins; otherwise the bean class's metadata
    (inherited through the MRO) applies. ``func`` is the plain function
    discovered by the AOP candidate walk — pass ``None`` for non-function
    attributes to fall back to the class-level mark.
    """
    meta = getattr(func, TX_META_ATTR, None) if func is not None else None
    if meta is None:
        meta = getattr(bean_cls, TX_META_ATTR, None)
    return meta if isinstance(meta, TransactionalMeta) else None


@overload
def Transactional(target: F, /) -> F: ...


@overload
def Transactional(
    target: None = None,
    /,
    *,
    propagation: Propagation = Propagation.REQUIRED,
    isolation: Isolation = Isolation.DEFAULT,
    read_only: bool = False,
    timeout: float | None = None,
    rollback_for: tuple[type[BaseException], ...] = (Exception,),
    no_rollback_for: tuple[type[BaseException], ...] = (),
    manager: str | None = None,
) -> Callable[[F], F]: ...


def Transactional(
    target: Any = None,
    /,
    *,
    propagation: Propagation = Propagation.REQUIRED,
    isolation: Isolation = Isolation.DEFAULT,
    read_only: bool = False,
    timeout: float | None = None,
    rollback_for: tuple[type[BaseException], ...] = (Exception,),
    no_rollback_for: tuple[type[BaseException], ...] = (),
    manager: str | None = None,
) -> Any:
    """Mark a method or class as a transactional boundary.

    Usable bare (``@Transactional``) or with arguments. ``manager`` names
    the bean to use as the transaction manager; ``None`` resolves by
    type — ``TransactionManager`` for sync methods,
    ``AsyncTransactionManager`` for async ones.
    """
    definition = TransactionDefinition(
        propagation=propagation,
        isolation=isolation,
        read_only=read_only,
        timeout_s=timeout,
        rollback_for=rollback_for,
        no_rollback_for=no_rollback_for,
    )
    meta = TransactionalMeta(definition=definition, manager=manager)

    def wrap(obj: Any) -> Any:
        setattr(obj, TX_META_ATTR, meta)
        markers: set[Any] = getattr(obj, MARKERS_ATTR, set())
        setattr(obj, MARKERS_ATTR, markers | {TRANSACTIONAL_MARKER})
        return obj

    if target is None:
        return wrap
    return wrap(target)
