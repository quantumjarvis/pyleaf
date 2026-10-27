"""Aspect-oriented programming support: pointcuts, aspects and advice.

This module implements the interception SPI that later packages
(transactions, caching, security, retry) plug into. Pointcuts are evaluated
statically at ``ApplicationContext.refresh()`` against
``(bean_type, method_name, function)`` triples discovered by walking the
bean class's MRO. Matched methods are patched onto each bean instance after
the BPP ``after_init`` phase, so self-invocation is intercepted too.
"""

from __future__ import annotations

import asyncio
import fnmatch
import functools
import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

from .definition import Scope
from .errors import PyleafError
from .stereotypes import COMPONENT_ATTR, ORDER_ATTR, ComponentMeta

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

ASPECT_ATTR = "__pyleaf_aspect__"
ADVICE_ATTR = "__pyleaf_advice__"
MARKERS_ATTR = "__pyleaf_markers__"

Predicate = Callable[[type[Any], str, Callable[..., Any]], bool]
Link = Callable[["Invocation"], Any]


class Pointcut:
    """A composable predicate deciding whether an advice applies to a method.

    Combine pointcuts with ``&`` (both must match), ``|`` (either matches)
    and ``~`` (negation).
    """

    __slots__ = ("_desc", "_predicate")

    def __init__(self, predicate: Predicate, desc: str) -> None:
        self._predicate = predicate
        self._desc = desc

    def matches(
        self, bean_cls: type[Any], method_name: str, func: Callable[..., Any]
    ) -> bool:
        """Return True when this pointcut applies to ``bean_cls.method_name``."""
        return self._predicate(bean_cls, method_name, func)

    def __and__(self, other: Pointcut) -> Pointcut:
        return Pointcut(
            lambda t, n, f: self.matches(t, n, f) and other.matches(t, n, f),
            f"({self._desc} & {other._desc})",
        )

    def __or__(self, other: Pointcut) -> Pointcut:
        return Pointcut(
            lambda t, n, f: self.matches(t, n, f) or other.matches(t, n, f),
            f"({self._desc} | {other._desc})",
        )

    def __invert__(self) -> Pointcut:
        return Pointcut(
            lambda t, n, f: not self.matches(t, n, f),
            f"~{self._desc}",
        )

    def __repr__(self) -> str:
        return self._desc


def within(prefix: str) -> Pointcut:
    """Match beans whose class ``__module__`` starts with ``prefix``."""

    def predicate(bean_cls: type[Any], _name: str, _func: Callable[..., Any]) -> bool:
        module = getattr(bean_cls, "__module__", "")
        return isinstance(module, str) and module.startswith(prefix)

    return Pointcut(predicate, f"within({prefix!r})")


def annotated(marker: Any) -> Pointcut:
    """Match methods (or whole classes) carrying ``marker`` in ``__pyleaf_markers__``.

    The marker convention: any decorator may record itself on the decorated
    object via ``obj.__pyleaf_markers__ |= {marker}`` (see :func:`mark`).
    """

    def predicate(bean_cls: type[Any], _name: str, func: Callable[..., Any]) -> bool:
        return marker in getattr(func, MARKERS_ATTR, ()) or marker in getattr(
            bean_cls, MARKERS_ATTR, ()
        )

    label = getattr(marker, "__name__", marker)
    return Pointcut(predicate, f"annotated({label!r})")


def name_matches(glob: str) -> Pointcut:
    """Match methods whose name matches the ``fnmatch`` glob ``glob``."""
    return Pointcut(
        lambda _t, n, _f: fnmatch.fnmatchcase(n, glob),
        f"name_matches({glob!r})",
    )


def bean_type(required: type[Any]) -> Pointcut:
    """Match beans whose class is a subclass of ``required``."""

    def predicate(bean_cls: type[Any], _name: str, _func: Callable[..., Any]) -> bool:
        return issubclass(bean_cls, required)

    label = getattr(required, "__name__", required)
    return Pointcut(predicate, f"bean_type({label!r})")


def mark(marker: Any) -> Callable[[F], F]:
    """Record ``marker`` in ``obj.__pyleaf_markers__`` for :func:`annotated`.

    Equivalent marker decorators may set the attribute themselves; both
    forms match ``annotated(marker)``.
    """

    def decorator(obj: F) -> F:
        markers: set[Any] = getattr(obj, MARKERS_ATTR, set())
        setattr(obj, MARKERS_ATTR, markers | {marker})
        return obj

    return decorator


def is_aspect(bean_cls: Any) -> bool:
    """Return True when ``bean_cls`` was decorated with :func:`Aspect`."""
    return isinstance(bean_cls, type) and bool(getattr(bean_cls, ASPECT_ATTR, False))


def Aspect(cls: type[T]) -> type[T]:
    """Mark a class as an aspect: a bean whose advice methods intercept calls.

    ``@Aspect`` also makes the class a component when it is not already one,
    so ``ctx.register(Cls)`` accepts aspect classes directly. Aspect beans
    are force-instantiated at ``refresh()`` (with BPPs), support injection,
    and are never themselves wrapped.
    """
    setattr(cls, ASPECT_ATTR, True)
    if getattr(cls, COMPONENT_ATTR, None) is None:
        setattr(cls, COMPONENT_ATTR, ComponentMeta(None, Scope.SINGLETON, True))
    return cls


@dataclass(frozen=True)
class AdviceSpec:
    """Metadata an advice decorator records on the advice function."""

    pointcut: Pointcut
    kind: str


def _advice(kind: str, pointcut: Pointcut) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        setattr(func, ADVICE_ATTR, AdviceSpec(pointcut, kind))
        return func

    return decorator


def Around(pointcut: Pointcut) -> Callable[[F], F]:
    """Wrap the whole invocation; the advice receives the Invocation and must
    call ``inv.proceed()`` to continue the chain (async advice awaits it)."""
    return _advice("around", pointcut)


def Before(pointcut: Pointcut) -> Callable[[F], F]:
    """Run before the target; the advice's return value is discarded."""
    return _advice("before", pointcut)


def After(pointcut: Pointcut) -> Callable[[F], F]:
    """Run after the target on both success and failure (finally semantics)."""
    return _advice("after", pointcut)


def AfterReturning(pointcut: Pointcut) -> Callable[[F], F]:
    """Run after the target only when it returns normally."""
    return _advice("after_returning", pointcut)


def AfterThrowing(pointcut: Pointcut) -> Callable[[F], F]:
    """Run after the target only when it raises; the exception re-raises."""
    return _advice("after_throwing", pointcut)


class Invocation:
    """A single intercepted call, handed to each interceptor in the chain.

    For async targets ``proceed()`` returns a coroutine which async advice
    awaits.
    """

    __slots__ = ("_index", "_links", "args", "kwargs", "method_name", "target")

    def __init__(
        self,
        target: Any,
        method_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        links: tuple[Link, ...],
    ) -> None:
        self.target = target
        self.method_name = method_name
        self.args = args
        self.kwargs = kwargs
        self._links = links
        self._index = 0

    def proceed(self) -> Any:
        """Invoke the next interceptor in the chain, or the target method."""
        link = self._links[self._index]
        self._index += 1
        return link(self)


@runtime_checkable
class MethodInterceptor(Protocol):
    """Extension point: an object able to intercept one invocation.

    Concrete interceptors are produced from ``@Aspect`` advice methods;
    later packages may implement this protocol directly.
    """

    def invoke(self, invocation: Invocation) -> Any:
        """Run the interception logic; call ``invocation.proceed()`` to continue."""
        ...


def iter_advices(aspect_cls: type[Any]) -> Iterator[tuple[str, Callable[..., Any]]]:
    """Yield ``(method_name, func)`` for each advice method on an aspect class."""
    seen: set[str] = set()
    for klass in aspect_cls.__mro__:
        for attr_name, member in vars(klass).items():
            if attr_name in seen:
                continue
            func = (
                member.__func__
                if isinstance(member, (staticmethod, classmethod))
                else member
            )
            if callable(func) and getattr(func, ADVICE_ATTR, None) is not None:
                seen.add(attr_name)
                yield attr_name, func


def iter_candidate_methods(
    bean_cls: type[Any],
) -> Iterator[tuple[str, Callable[..., Any]]]:
    """Yield ``(method_name, func)`` for plain instance methods that may be advised.

    Only plain functions found via the MRO ``vars()`` walk are candidates:
    underscore-prefixed names, ``property``/``cached_property`` descriptors,
    classmethods, staticmethods and non-callable attributes are never
    intercepted.
    """
    seen: set[str] = set()
    for klass in bean_cls.__mro__:
        for attr_name, member in vars(klass).items():
            if attr_name in seen:
                continue
            seen.add(attr_name)
            if attr_name.startswith("_") or not inspect.isfunction(member):
                continue
            yield attr_name, member


def advice_order(func: Callable[..., Any], class_order: int) -> int:
    """Effective order of an advice: method-level ``@Order`` wins over the
    aspect class's ``@Order``; lower runs outermost."""
    order = getattr(func, ORDER_ATTR, class_order)
    return order if isinstance(order, int) else class_order


def compile_link(call: Link, kind: str, is_async: bool) -> Link:
    """Compile one bound advice into a chain link for a sync or async target.

    Non-``around`` advices are sugar compiled to interceptor equivalents:
    ``before`` discards its result, ``after`` acts as ``finally``,
    ``after_returning`` runs only on success and ``after_throwing`` only on
    failure before re-raising the original exception.
    """
    if kind == "around":
        return call
    if is_async:
        return _compile_async(call, kind)
    return _compile_sync(call, kind)


def _compile_sync(call: Link, kind: str) -> Link:
    if kind == "before":

        def before(inv: Invocation) -> Any:
            call(inv)
            return inv.proceed()

        return before
    if kind == "after":

        def after(inv: Invocation) -> Any:
            try:
                return inv.proceed()
            finally:
                call(inv)

        return after
    if kind == "after_returning":

        def after_returning(inv: Invocation) -> Any:
            result = inv.proceed()
            call(inv)
            return result

        return after_returning
    if kind == "after_throwing":

        def after_throwing(inv: Invocation) -> Any:
            try:
                return inv.proceed()
            except Exception:
                call(inv)
                raise

        return after_throwing
    raise PyleafError(f"Unknown advice kind: {kind!r}")


def _compile_async(call: Link, kind: str) -> Link:
    if kind == "before":

        async def before(inv: Invocation) -> Any:
            await call(inv)
            return await inv.proceed()

        return before
    if kind == "after":

        async def after(inv: Invocation) -> Any:
            try:
                return await inv.proceed()
            finally:
                await call(inv)

        return after
    if kind == "after_returning":

        async def after_returning(inv: Invocation) -> Any:
            result = await inv.proceed()
            await call(inv)
            return result

        return after_returning
    if kind == "after_throwing":

        async def after_throwing(inv: Invocation) -> Any:
            try:
                return await inv.proceed()
            except Exception:
                await call(inv)
                raise

        return after_throwing
    raise PyleafError(f"Unknown advice kind: {kind!r}")


def make_wrapper(
    instance: Any,
    method_name: str,
    func: Callable[..., Any],
    links: list[Link],
) -> Callable[..., Any]:
    """Build the bound wrapper patched onto ``instance`` for ``method_name``.

    The wrapper preserves ``inspect.signature``, ``__name__`` and ``__doc__``
    via ``functools.wraps`` on the original bound method.
    """
    bound: Callable[..., Any] = getattr(instance, method_name)

    def terminal(inv: Invocation) -> Any:
        return bound(*inv.args, **inv.kwargs)

    chain = (*links, terminal)
    if asyncio.iscoroutinefunction(func):

        @functools.wraps(bound)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            inv = Invocation(instance, method_name, args, kwargs, chain)
            return await inv.proceed()

    else:

        @functools.wraps(bound)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            inv = Invocation(instance, method_name, args, kwargs, chain)
            return inv.proceed()

    return wrapper
