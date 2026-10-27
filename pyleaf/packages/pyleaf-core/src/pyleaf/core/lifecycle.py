"""Lifecycle method decorators: ``@PostConstruct`` and ``@PreDestroy``."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

POST_CONSTRUCT_ATTR = "__pyleaf_post_construct__"
PRE_DESTROY_ATTR = "__pyleaf_pre_destroy__"


def PostConstruct(func: F) -> F:
    """Mark a method to run once after construction and post-processing.

    Applies to every created instance, singletons and prototypes alike.
    """
    setattr(func, POST_CONSTRUCT_ATTR, True)
    return func


def PreDestroy(func: F) -> F:
    """Mark a method to run on ``ctx.close()`` for singleton beans only."""
    setattr(func, PRE_DESTROY_ATTR, True)
    return func


def lifecycle_methods(cls: type[Any], marker: str) -> Iterator[str]:
    """Yield names of ``cls`` methods carrying ``marker``.

    Walks the MRO like :func:`pyleaf.core.context._iter_bean_methods`: the
    first definition carrying the marker wins per attribute name.
    """
    seen: set[str] = set()
    for klass in cls.__mro__:
        for attr_name, member in vars(klass).items():
            if attr_name in seen:
                continue
            func = member
            if isinstance(member, (staticmethod, classmethod)):
                func = member.__func__
            marked = getattr(member, marker, None) is not None or (
                callable(func) and getattr(func, marker, None) is not None
            )
            if callable(func) and marked:
                seen.add(attr_name)
                yield attr_name
