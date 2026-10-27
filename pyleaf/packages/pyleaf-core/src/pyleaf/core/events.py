"""Application events: ``@EventListener`` methods and ``EventPublisher``.

Beans declare listeners by annotating a method with :func:`EventListener`;
the single non-self parameter's type annotation is the event type. The
context builds a static listener table at ``refresh()`` — resolved from
type hints, no instantiation needed — force-instantiates listener beans
like post-processors and aspects, then dispatches published events to
every listener whose declared type is a superclass of the event's type.
Events are plain objects: no base class or interface is required.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

F = TypeVar("F", bound=Callable[..., Any])

EVENT_LISTENER_ATTR = "__pyleaf_eventlistener__"


def EventListener(func: F) -> F:
    """Mark a bean method as an event listener.

    The method's single non-self parameter annotation declares the event
    type it listens for: the listener fires for any published event that is
    an instance of it, subclasses included. Listener methods may be
    ``async def`` and may carry ``@Order``.
    """
    setattr(func, EVENT_LISTENER_ATTR, True)
    return func


@runtime_checkable
class EventPublisher(Protocol):
    """Structural type for the object beans inject to publish events.

    ``ApplicationContext`` registers itself as the ``eventPublisher`` bean,
    so a ``pub: EventPublisher`` constructor parameter resolves to the
    context itself.
    """

    def publish(self, event: object) -> None:
        """Dispatch ``event`` to every matching listener."""
        ...

    async def publish_async(self, event: object) -> None:
        """Dispatch ``event``, awaiting async listeners sequentially."""
        ...


@dataclass(frozen=True)
class ListenerEntry:
    """One ``@EventListener`` method resolved against the bean registry."""

    bean_name: str
    method_name: str
    event_type: Any
    is_async: bool
    order: int


def event_matches(event_type: type[Any], declared: Any) -> bool:
    """True when a listener declared for ``declared`` accepts ``event_type``.

    ``issubclass`` covers base classes and runtime-checkable Protocols.
    Non-class declared types (unions, generic aliases) raise ``TypeError``
    and simply never match.
    """
    try:
        return issubclass(event_type, declared)
    except TypeError:
        return False


def iter_event_listeners(cls: type[Any]) -> Iterator[tuple[str, Callable[..., Any]]]:
    """Yield ``(method_name, func)`` for each ``@EventListener`` method.

    Walks the MRO like :func:`pyleaf.core.lifecycle.lifecycle_methods`: the
    first definition carrying the marker wins per attribute name, and
    iteration order matches declaration order within each class.
    """
    seen: set[str] = set()
    for klass in cls.__mro__:
        for attr_name, member in vars(klass).items():
            if attr_name in seen:
                continue
            func = member
            if isinstance(member, (staticmethod, classmethod)):
                func = member.__func__
            marked = getattr(member, EVENT_LISTENER_ATTR, None) is not None or (
                callable(func) and getattr(func, EVENT_LISTENER_ATTR, None) is not None
            )
            if callable(func) and marked:
                seen.add(attr_name)
                yield attr_name, func
