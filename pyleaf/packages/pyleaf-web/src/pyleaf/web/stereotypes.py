"""Web stereotypes: ``@RestController`` and HTTP method mapping decorators.

``@RestController`` attaches ordinary component metadata (so the existing
``ApplicationContext.register`` dispatch, DI, conditions and AOP apply
unchanged) plus a ``__pyleaf_restcontroller__`` marker carrying the path
prefix. The ``@*Mapping`` decorators attach a list of ``RouteMeta`` under
``__pyleaf_route__``; nothing is routed until ``build_router`` runs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar, overload

from pyleaf.core.definition import Scope
from pyleaf.core.stereotypes import COMPONENT_ATTR, ComponentMeta

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

REST_CONTROLLER_ATTR = "__pyleaf_restcontroller__"
ROUTE_ATTR = "__pyleaf_route__"


@dataclass(frozen=True)
class RestControllerMeta:
    """Metadata carried by a ``@RestController`` class."""

    prefix: str


@dataclass(frozen=True)
class RouteMeta:
    """One HTTP route declared on a controller method.

    Stored in a list under :data:`ROUTE_ATTR` so several mappings may
    stack on a single method. ``kwargs`` pass through to
    ``APIRouter.add_api_route``.
    """

    path: str
    methods: tuple[str, ...]
    status_code: int | None
    response_model: Any
    kwargs: dict[str, Any] = field(default_factory=dict)


@overload
def RestController(target: type[T], /) -> type[T]: ...


@overload
def RestController(
    target: str | None = None,
    /,
    *,
    prefix: str = "",
) -> Callable[[type[T]], type[T]]: ...


def RestController(
    target: type[T] | str | None = None,
    /,
    *,
    prefix: str = "",
) -> type[T] | Callable[[type[T]], type[T]]:
    """Mark a class as a REST controller bean.

    Usable bare (``@RestController``) or with a path prefix
    (``@RestController("/todos")`` / ``@RestController(prefix="/todos")``).
    The class registers through normal component dispatch — constructor
    injection, ``@Order``, conditions and AOP all apply — while
    :func:`pyleaf.web.build_router` turns its ``@*Mapping`` methods into
    FastAPI routes.
    """
    effective = prefix or (target if isinstance(target, str) else "")

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, REST_CONTROLLER_ATTR, RestControllerMeta(effective))
        if getattr(cls, COMPONENT_ATTR, None) is None:
            setattr(
                cls, COMPONENT_ATTR, ComponentMeta(None, Scope.SINGLETON, True)
            )
        return cls

    if target is None or isinstance(target, str):
        return wrap
    return wrap(target)


def _mapping(
    http_methods: tuple[str, ...],
    path: str | F,
    *,
    status_code: int | None,
    response_model: Any,
    kwargs: dict[str, Any],
) -> F | Callable[[F], F]:
    target: F | None = None
    if not isinstance(path, str):
        # Bare ``@GetMapping`` usage: ``path`` is the decorated function.
        target = path
        path = ""

    meta = RouteMeta(
        path=path,
        methods=http_methods,
        status_code=status_code,
        response_model=response_model,
        kwargs=kwargs,
    )

    def wrap(func: F) -> F:
        routes: list[RouteMeta] = list(getattr(func, ROUTE_ATTR, ()))
        # Prepend so stacked decorators register top-down, as written.
        routes.insert(0, meta)
        setattr(func, ROUTE_ATTR, routes)
        return func

    if target is not None:
        return wrap(target)
    return wrap


@overload
def GetMapping(path: F, /) -> F: ...


@overload
def GetMapping(
    path: str = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> Callable[[F], F]: ...


def GetMapping(
    path: str | F = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> F | Callable[[F], F]:
    """Map a controller method to HTTP GET ``path`` (relative to the prefix)."""
    return _mapping(
        ("GET",),
        path,
        status_code=status_code,
        response_model=response_model,
        kwargs=kwargs,
    )


@overload
def PostMapping(path: F, /) -> F: ...


@overload
def PostMapping(
    path: str = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> Callable[[F], F]: ...


def PostMapping(
    path: str | F = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> F | Callable[[F], F]:
    """Map a controller method to HTTP POST ``path``."""
    return _mapping(
        ("POST",),
        path,
        status_code=status_code,
        response_model=response_model,
        kwargs=kwargs,
    )


@overload
def PutMapping(path: F, /) -> F: ...


@overload
def PutMapping(
    path: str = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> Callable[[F], F]: ...


def PutMapping(
    path: str | F = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> F | Callable[[F], F]:
    """Map a controller method to HTTP PUT ``path``."""
    return _mapping(
        ("PUT",),
        path,
        status_code=status_code,
        response_model=response_model,
        kwargs=kwargs,
    )


@overload
def PatchMapping(path: F, /) -> F: ...


@overload
def PatchMapping(
    path: str = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> Callable[[F], F]: ...


def PatchMapping(
    path: str | F = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> F | Callable[[F], F]:
    """Map a controller method to HTTP PATCH ``path``."""
    return _mapping(
        ("PATCH",),
        path,
        status_code=status_code,
        response_model=response_model,
        kwargs=kwargs,
    )


@overload
def DeleteMapping(path: F, /) -> F: ...


@overload
def DeleteMapping(
    path: str = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> Callable[[F], F]: ...


def DeleteMapping(
    path: str | F = "",
    /,
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> F | Callable[[F], F]:
    """Map a controller method to HTTP DELETE ``path``."""
    return _mapping(
        ("DELETE",),
        path,
        status_code=status_code,
        response_model=response_model,
        kwargs=kwargs,
    )


@overload
def RequestMapping(path: F, /) -> F: ...


@overload
def RequestMapping(
    path: str = "",
    /,
    methods: Sequence[str] = ("GET",),
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> Callable[[F], F]: ...


def RequestMapping(
    path: str | F = "",
    /,
    methods: Sequence[str] = ("GET",),
    *,
    status_code: int | None = None,
    response_model: Any = None,
    **kwargs: Any,
) -> F | Callable[[F], F]:
    """Map a controller method to one or more HTTP methods on ``path``."""
    return _mapping(
        tuple(methods),
        path,
        status_code=status_code,
        response_model=response_model,
        kwargs=kwargs,
    )
