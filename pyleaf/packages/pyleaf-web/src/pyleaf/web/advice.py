"""``@ControllerAdvice`` beans and ``@ExceptionHandler`` methods.

``@ControllerAdvice`` attaches ordinary component metadata (so registration,
DI, conditions and AOP apply unchanged) plus a
``__pyleaf_controlleradvice__`` marker. ``@ExceptionHandler(*exc_types,
status_code=...)`` attaches an ``ExceptionHandlerMeta`` under
``__pyleaf_exceptionhandler__``; nothing is wired until ``register_advice``
runs.

Dispatch mechanics
------------------
``register_advice`` installs ONE pure-ASGI dispatcher middleware via
``app.add_middleware``. Registering a generic ``Exception`` handler through
``app.add_exception_handler`` was considered and rejected: FastAPI routes
the ``Exception`` (and ``500``) keys to ``ServerErrorMiddleware``, which
*always* re-raises after the handler returns — a handled exception would
still propagate to the server/TestClient. As middleware instead, the
dispatcher sits

* inside ``ServerErrorMiddleware`` — unhandled exceptions still produce the
  default 500 (and re-raise for the server), and
* outside ``ExceptionMiddleware`` — FastAPI's own ``HTTPException`` /
  ``RequestValidationError`` handlers keep working at the inner layer.

At request time the dispatcher walks ``type(exc).__mro__`` and picks the
handler whose declared exception type is closest to the actual exception
(MRO distance — more specific wins across advice beans too). Ties are
broken by the advice bean's ``@Order`` (lowest first), then by declaration
sequence. Handlers may be sync or async and may declare parameters
injectable from ``(request: Request, exc: <exception>)``, resolved by
annotation first, then by conventional names (``request``/``req``,
``exc``/``exception``/``error``/``e``). A ``Response`` return is used
verbatim; anything else (dict, list, pydantic model, scalar) becomes a
``JSONResponse`` with the decorator's ``status_code`` (default 500).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, get_type_hints, overload

from fastapi import FastAPI
from pydantic import BaseModel
from pyleaf.core.definition import Scope
from pyleaf.core.stereotypes import COMPONENT_ATTR, ComponentMeta
from starlette._utils import is_async_callable
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Send
from starlette.types import Scope as ASGIScope

if TYPE_CHECKING:
    from pyleaf.core.context import ApplicationContext

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

CONTROLLER_ADVICE_ATTR = "__pyleaf_controlleradvice__"
EXCEPTION_HANDLER_ATTR = "__pyleaf_exceptionhandler__"

_REQUEST_PARAM_NAMES = frozenset({"request", "req"})
_EXC_PARAM_NAMES = frozenset({"exc", "exception", "error", "e"})


@dataclass(frozen=True)
class ExceptionHandlerMeta:
    """Metadata carried by an ``@ExceptionHandler`` method."""

    exc_types: tuple[type[BaseException], ...]
    status_code: int


@dataclass(frozen=True)
class _AdviceHandler:
    """One resolved advice method, ready to dispatch."""

    exc_types: tuple[type[BaseException], ...]
    status_code: int
    order: int
    seq: int
    func: Callable[..., Any]
    plan: tuple[str, ...]  # "request" | "exc", parallel to the method's params


@overload
def ControllerAdvice(target: type[T], /) -> type[T]: ...


@overload
def ControllerAdvice(
    target: str | None = None,
    /,
    *,
    name: str | None = None,
) -> Callable[[type[T]], type[T]]: ...


def ControllerAdvice(
    target: type[T] | str | None = None,
    /,
    *,
    name: str | None = None,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Mark a class as a controller-advice bean.

    Usable bare (``@ControllerAdvice``) or with a bean name
    (``@ControllerAdvice("advice")`` / ``@ControllerAdvice(name="advice")``).
    The class registers through normal component dispatch — constructor
    injection, ``@Order``, conditions and AOP all apply — while
    :func:`register_advice` wires its ``@ExceptionHandler`` methods into the
    app.
    """
    effective = name if name is not None else (target if isinstance(target, str) else None)

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, CONTROLLER_ADVICE_ATTR, True)
        if getattr(cls, COMPONENT_ATTR, None) is None:
            setattr(cls, COMPONENT_ATTR, ComponentMeta(effective, Scope.SINGLETON, True))
        return cls

    if target is None or isinstance(target, str):
        return wrap
    return wrap(target)


def ExceptionHandler(
    *exc_types: type[BaseException],
    status_code: int = 500,
) -> Callable[[F], F]:
    """Mark an advice-bean method as the handler for ``exc_types``.

    At least one exception class is required. ``status_code`` is applied
    only when the handler's return value is converted to a ``JSONResponse``;
    a returned ``Response`` keeps its own status.
    """
    if not exc_types:
        raise ValueError("@ExceptionHandler requires at least one exception type")
    for exc_type in exc_types:
        if not (isinstance(exc_type, type) and issubclass(exc_type, BaseException)):
            raise TypeError(
                f"@ExceptionHandler expects exception classes, got {exc_type!r}"
            )
    meta = ExceptionHandlerMeta(exc_types=tuple(exc_types), status_code=status_code)

    def wrap(func: F) -> F:
        setattr(func, EXCEPTION_HANDLER_ATTR, meta)
        return func

    return wrap


def _iter_handler_methods(
    cls: type[Any],
) -> Iterator[tuple[str, ExceptionHandlerMeta]]:
    """Yield ``(method_name, meta)`` in MRO declaration order."""
    seen: set[str] = set()
    for klass in cls.__mro__:
        for attr_name, member in vars(klass).items():
            if attr_name in seen:
                continue
            seen.add(attr_name)
            func = (
                member.__func__
                if isinstance(member, (staticmethod, classmethod))
                else member
            )
            meta = getattr(func, EXCEPTION_HANDLER_ATTR, None)
            if meta is not None:
                yield attr_name, meta


def _build_plan(func: Callable[..., Any], owner: str) -> tuple[str, ...]:
    """Map each handler parameter to "request" or "exc".

    Resolution: annotation first (``Request`` subclass -> request,
    ``BaseException`` subclass -> exc), then conventional parameter names.
    Anything else fails fast at registration.
    """
    try:
        hints = get_type_hints(getattr(func, "__func__", func))
    except Exception:
        hints = {}
    plan: list[str] = []
    for pname, param in inspect.signature(func).parameters.items():
        ann = hints.get(pname)
        if ann is None and param.annotation is not inspect.Parameter.empty:
            ann = param.annotation
        if isinstance(ann, type) and issubclass(ann, BaseException):
            plan.append("exc")
        elif isinstance(ann, type) and issubclass(ann, Request):
            plan.append("request")
        elif pname in _REQUEST_PARAM_NAMES:
            plan.append("request")
        elif pname in _EXC_PARAM_NAMES:
            plan.append("exc")
        else:
            raise TypeError(
                f"Cannot resolve parameter '{pname}' of exception handler "
                f"'{owner}': annotate it Request or an exception type, or name "
                f"it one of {sorted(_REQUEST_PARAM_NAMES | _EXC_PARAM_NAMES)}"
            )
    return tuple(plan)


def _resolve(handlers: list[_AdviceHandler], exc: Exception) -> _AdviceHandler | None:
    """Pick the handler whose declared type is closest to ``exc``'s type.

    Winner = smallest ``(mro_distance, bean @Order, declaration seq)``.
    """
    mro = type(exc).__mro__
    best: _AdviceHandler | None = None
    best_key: tuple[int, int, int] | None = None
    for handler in handlers:
        distance = min(
            (mro.index(t) for t in handler.exc_types if t in mro),
            default=None,
        )
        if distance is None:
            continue
        key = (distance, handler.order, handler.seq)
        if best_key is None or key < best_key:
            best, best_key = handler, key
    return best


async def _invoke(handler: _AdviceHandler, request: Request, exc: Exception) -> Response:
    args = [request if kind == "request" else exc for kind in handler.plan]
    if is_async_callable(handler.func):
        result = await handler.func(*args)
    else:
        result = await run_in_threadpool(handler.func, *args)
    if inspect.isawaitable(result):
        # Sync wrapper (e.g. an AOP proxy) returning a coroutine.
        result = await result
    if isinstance(result, Response):
        return result
    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    return JSONResponse(result, status_code=handler.status_code)


class _ControllerAdviceMiddleware:
    """ASGI middleware dispatching exceptions to ``@ExceptionHandler`` methods.

    See the module docstring for why this is middleware rather than an
    ``add_exception_handler`` registration.
    """

    def __init__(self, app: ASGIApp, handlers: list[_AdviceHandler]) -> None:
        self.app = app
        self._handlers = handlers

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if response_started:
                raise
            handler = _resolve(self._handlers, exc)
            if handler is None:
                raise
            request = Request(scope, receive, send)
            response = await _invoke(handler, request, exc)
            await response(scope, receive, send)


def register_advice(app: FastAPI, ctx: ApplicationContext) -> FastAPI:
    """Wire every ``@ControllerAdvice`` bean's handlers into ``app``.

    Advice beans are instantiated through the container (constructor DI,
    post-processors, AOP), sorted by ``(@Order, registration order)``, and
    their ``@ExceptionHandler`` methods are collected in declaration order.
    A single dispatcher middleware is installed; see the module docstring
    for the resolution rules. Returns ``app`` for chaining.
    """
    defs = [
        defn
        for defn in ctx.bean_definitions()
        if isinstance(defn.bean_type, type)
        and getattr(defn.bean_type, CONTROLLER_ADVICE_ATTR, None) is not None
    ]
    # ``bean_definitions()`` is registration order; the sort is stable.
    defs.sort(key=lambda d: d.order)
    handlers: list[_AdviceHandler] = []
    seq = 0
    for defn in defs:
        instance = ctx.get_bean(defn.name)
        for method_name, meta in _iter_handler_methods(defn.bean_type):
            func: Callable[..., Any] = getattr(instance, method_name)
            plan = _build_plan(func, f"{defn.name}.{method_name}")
            handlers.append(
                _AdviceHandler(
                    exc_types=meta.exc_types,
                    status_code=meta.status_code,
                    order=defn.order,
                    seq=seq,
                    func=func,
                    plan=plan,
                )
            )
            seq += 1
    if handlers:
        app.add_middleware(_ControllerAdviceMiddleware, handlers=handlers)
    return app


__all__ = [
    "CONTROLLER_ADVICE_ATTR",
    "EXCEPTION_HANDLER_ATTR",
    "ControllerAdvice",
    "ExceptionHandler",
    "ExceptionHandlerMeta",
    "register_advice",
]
