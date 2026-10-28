"""Turn ``@RestController`` beans into FastAPI routes.

``build_router`` is explicit — nothing is registered at import or
``refresh()`` time. For every bean definition whose type carries the
rest-controller marker it resolves the bean instance (so constructor DI,
post-processors and AOP have all run), walks its methods in MRO
declaration order and mounts each ``@*Mapping`` route via
``router.add_api_route``.

Bound-method signatures: FastAPI inspects endpoints with
``inspect.signature``, which already drops ``self`` for bound methods.
AOP-advised methods return the ``functools.wraps`` wrapper patched onto
the instance (see ``pyleaf.core.aop.make_wrapper``); the ``__wrapped__``
chain resolves to the bound method, so the signature — and therefore
FastAPI's parameter/dependency resolution — is preserved too.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, TypeVar

from fastapi import APIRouter, FastAPI

from .stereotypes import REST_CONTROLLER_ATTR, ROUTE_ATTR, RestControllerMeta, RouteMeta

if TYPE_CHECKING:
    from pyleaf.core.context import ApplicationContext

RouterT = TypeVar("RouterT", FastAPI, APIRouter)


def _iter_route_methods(cls: type[Any]) -> Iterator[tuple[str, list[RouteMeta]]]:
    """Yield ``(method_name, routes)`` in MRO declaration order.

    ``vars()`` preserves declaration order within each class; the MRO walk
    makes subclass attributes shadow base-class ones, matching normal
    attribute lookup.
    """
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
            routes = getattr(func, ROUTE_ATTR, None)
            if routes:
                yield attr_name, list(routes)


def _join(prefix: str, path: str) -> str:
    """Join a controller prefix and a route path into one absolute path."""
    joined = prefix + path
    if not joined.startswith("/"):
        joined = "/" + joined
    return joined


def build_router(ctx: ApplicationContext) -> APIRouter:
    """Collect every ``@RestController`` bean's routes into an APIRouter.

    Controller definitions are sorted by ``(@Order, registration order)``
    and each controller's methods are mounted in declaration order, so the
    route table is deterministic.
    """
    router = APIRouter()
    defs = [
        defn
        for defn in ctx._registry.all()
        if isinstance(defn.bean_type, type)
        and getattr(defn.bean_type, REST_CONTROLLER_ATTR, None) is not None
    ]
    # ``BeanRegistry.all()`` is registration order; the sort is stable.
    defs.sort(key=lambda d: d.order)
    for defn in defs:
        instance = ctx.get_bean(defn.name)
        meta: RestControllerMeta = getattr(defn.bean_type, REST_CONTROLLER_ATTR)
        for method_name, routes in _iter_route_methods(defn.bean_type):
            endpoint: Callable[..., Any] = getattr(instance, method_name)
            for route in routes:
                kwargs = dict(route.kwargs)
                # ``None`` is passed through for status_code (FastAPI's own
                # default) but NOT for response_model: add_api_route's default
                # is Default(None), which enables return-annotation inference
                # — passing plain None would switch response models off.
                if route.status_code is not None:
                    kwargs["status_code"] = route.status_code
                if route.response_model is not None:
                    kwargs["response_model"] = route.response_model
                router.add_api_route(
                    _join(meta.prefix, route.path),
                    endpoint,
                    methods=list(route.methods),
                    **kwargs,
                )
    return router


def register_controllers(target: RouterT, ctx: ApplicationContext) -> RouterT:
    """Mount every ``@RestController`` bean's routes on ``target``.

    ``target`` may be a ``FastAPI`` application or an ``APIRouter``; the
    same object is returned for chaining.
    """
    target.include_router(build_router(ctx))
    return target
