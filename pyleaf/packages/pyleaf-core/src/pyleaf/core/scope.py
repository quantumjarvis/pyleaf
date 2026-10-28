"""Request scope: a contextvar-bound per-request bean cache plus the proxy.

``Scope.REQUEST`` beans are never instantiated by the container directly:
``ctx.get`` / injection always yields a :class:`RequestScopedProxy`. The
proxy resolves the real instance lazily on every attribute access through
:class:`RequestScope`, a per-request cache bound to the current execution
context via a :class:`contextvars.ContextVar`. ``RequestScopeMiddleware``
(pyleaf-web) activates one scope per HTTP request; asyncio tasks and
thread-copied contexts each see their own scope.

Proxy semantics (pinned by tests): ``__getattr__``/``__setattr__`` and the
common dunders forward to the current scope's instance, so a singleton
holding one proxy transparently sees a different backing instance per
request. ``proxy.__class__`` stays ``RequestScopedProxy`` — ``isinstance``
against the bean type is NOT supported (unlike Spring's class-based
proxies). Access outside an active scope raises :class:`ScopeNotActive`
naming the bean.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from types import TracebackType
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from .errors import ScopeNotActive
from .lifecycle import PRE_DESTROY_ATTR, lifecycle_methods

if TYPE_CHECKING:
    from .context import ApplicationContext
    from .definition import BeanDefinition

T = TypeVar("T")

_logger = logging.getLogger(__name__)

_current: ContextVar[RequestScope | None] = ContextVar(
    "pyleaf_request_scope", default=None
)


def _type_name(tp: Any) -> str:
    name = getattr(tp, "__name__", None)
    return name if isinstance(name, str) else str(tp)


class RequestScope:
    """One active request scope: a per-request cache of scoped bean instances.

    Activate with :meth:`activate`/:meth:`deactivate` (token pair, as the
    ASGI middleware does) or as a context manager — ``with RequestScope(ctx)``
    activates on entry and deactivates + :meth:`close` on exit. Instances
    are created through the owning context's normal factory path, so a
    request bean's own dependencies resolve inside the active scope.
    """

    def __init__(self, context: ApplicationContext) -> None:
        self._context = context
        self._instances: dict[str, Any] = {}
        self._created: list[Any] = []
        self._token: Token[RequestScope | None] | None = None

    @classmethod
    def current(cls) -> RequestScope:
        """Return the scope active in this context; raise ScopeNotActive."""
        scope = _current.get()
        if scope is None:
            raise ScopeNotActive("No request scope is active in this context")
        return scope

    @classmethod
    def active(cls) -> RequestScope | None:
        """Return the active scope or ``None`` (no exception)."""
        return _current.get()

    def activate(self) -> Token[RequestScope | None]:
        """Bind this scope to the current context; returns a reset token."""
        return _current.set(self)

    @staticmethod
    def deactivate(token: Token[RequestScope | None]) -> None:
        """Undo a previous :meth:`activate` using its token."""
        _current.reset(token)

    def __enter__(self) -> RequestScope:
        self._token = self.activate()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._token is not None:
            self.deactivate(self._token)
            self._token = None
        # Teardown runs after deactivation: a @PreDestroy hook that reaches
        # for a request bean sees ScopeNotActive rather than silently
        # creating an instance nothing will destroy.
        self.close()

    def get_or_create(self, defn: BeanDefinition) -> Any:
        """Return the scope-cached instance for ``defn``, creating it once.

        Creation goes through the context's normal pipeline (factory,
        post-processors, @PostConstruct, AOP) but the instance is cached
        here — per scope, keyed by bean name — not in the singleton cache.
        """
        instance = self._instances.get(defn.name)
        if instance is None:
            instance = self._context._instantiate(defn)
            self._instances[defn.name] = instance
            self._created.append(instance)
        return instance

    def close(self) -> None:
        """Run @PreDestroy on scoped instances (reverse creation order).

        Like ``ctx.close()`` for singletons: a failing hook is logged and
        does not prevent the remaining instances from being destroyed. The
        cache is cleared either way; already-closed scopes are a no-op.
        """
        for instance in reversed(self._created):
            for method_name in lifecycle_methods(type(instance), PRE_DESTROY_ATTR):
                try:
                    getattr(instance, method_name)()
                except Exception:
                    _logger.exception(
                        "Error running @PreDestroy '%s' on bean %r",
                        method_name,
                        instance,
                    )
        self._created.clear()
        self._instances.clear()


class RequestScopedProxy(Generic[T]):
    """Lazy handle injected for a ``Scope.REQUEST`` dependency.

    Every attribute access resolves the current scope's instance, so one
    proxy serves a different backing instance per request. ``__class__``
    deliberately stays ``RequestScopedProxy``: ``isinstance(proxy, T)`` is
    NOT supported. Outside an active scope any forwarded access raises
    :class:`ScopeNotActive` naming the bean.
    """

    __slots__ = ("_proxy_context", "_proxy_defn")

    _INTERNAL = frozenset({"_proxy_context", "_proxy_defn"})

    def __init__(self, context: ApplicationContext, defn: BeanDefinition) -> None:
        object.__setattr__(self, "_proxy_context", context)
        object.__setattr__(self, "_proxy_defn", defn)

    def _target(self) -> T:
        defn: BeanDefinition = self._proxy_defn
        scope = RequestScope.active()
        if scope is None:
            raise ScopeNotActive(
                f"Request-scoped bean '{defn.name}'"
                f" ({_type_name(defn.bean_type)}) accessed outside"
                " an active request scope"
            )
        return cast(T, scope.get_or_create(defn))

    def __getattr__(self, name: str) -> Any:
        # Only reached when normal lookup fails (slots, class attrs and
        # the dunders below are all found first), so internals never
        # recurse through here.
        return getattr(self._target(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in RequestScopedProxy._INTERNAL:
            object.__setattr__(self, name, value)
        else:
            setattr(self._target(), name, value)

    def __repr__(self) -> str:
        return repr(self._target())

    def __str__(self) -> str:
        return str(self._target())

    def __eq__(self, other: object) -> bool:
        target: Any = self._target()
        if isinstance(other, RequestScopedProxy):
            other = other._target()
        return bool(target == other)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash(self._target())

    def __bool__(self) -> bool:
        return bool(self._target())

    def __len__(self) -> int:
        target: Any = self._target()
        return len(target)

    def __iter__(self) -> Any:
        target: Any = self._target()
        return iter(target)

    def __contains__(self, item: Any) -> bool:
        target: Any = self._target()
        return item in target

    def __getitem__(self, key: Any) -> Any:
        target: Any = self._target()
        return target[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        target: Any = self._target()
        target[key] = value

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        target: Any = self._target()
        return target(*args, **kwargs)


__all__ = [
    "RequestScope",
    "RequestScopedProxy",
    "ScopeNotActive",
]
