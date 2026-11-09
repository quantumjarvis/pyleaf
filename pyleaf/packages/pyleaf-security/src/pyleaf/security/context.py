"""SecurityContextHolder: the per-context authentication, via a contextvar.

Same shape as ``pyleaf.core.scope.RequestScope`` / the tx
``TransactionSynchronizationManager``: a single ``ContextVar`` holds the
current ``Authentication`` (or ``None``). asyncio tasks and
``contextvars.copy_context()`` copies see the value as of copy time and
cannot leak mutations back — two concurrent tasks each set their own
authentication without affecting each other or the parent.

``with_authentication(auth)`` is token-based set/restore (``set`` +
``reset``), usable both as a context manager and as a decorator —
mirroring the request-scope ``activate``/``deactivate`` pair in
pyleaf-web's middleware.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, TypeVar, cast

from .core import Authentication
from .errors import AuthenticationError

F = TypeVar("F", bound=Callable[..., Any])

_current: ContextVar[Authentication | None] = ContextVar(
    "pyleaf_security_authentication", default=None
)


class _BoundAuthentication:
    """Token-restoring authentication scope: context manager + decorator.

    ``__call__`` binds a FRESH scope per invocation so a decorated
    function is reentrant — each call sets and restores its own token.
    """

    def __init__(self, authentication: Authentication) -> None:
        self._authentication = authentication
        self._token: Token[Authentication | None] | None = None

    def __enter__(self) -> Authentication:
        self._token = _current.set(self._authentication)
        return self._authentication

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._token is not None:
            _current.reset(self._token)
            self._token = None

    def __call__(self, func: F) -> F:
        @functools.wraps(func)
        def inner(*args: Any, **kwargs: Any) -> Any:
            with _BoundAuthentication(self._authentication):
                return func(*args, **kwargs)

        return cast(F, inner)


class SecurityContextHolder:
    """Static API over the per-context authentication.

    All methods are classmethods — the holder is never instantiated;
    state lives in the contextvar, not on the class.
    """

    @classmethod
    def get_authentication(cls) -> Authentication | None:
        """Return the current authentication, or ``None`` when unset."""
        return _current.get()

    @classmethod
    def set_authentication(
        cls, authentication: Authentication | None
    ) -> Token[Authentication | None]:
        """Bind an authentication to this context; returns a reset token."""
        return _current.set(authentication)

    @classmethod
    def reset(cls, token: Token[Authentication | None]) -> None:
        """Undo a previous :meth:`set_authentication` using its token."""
        _current.reset(token)

    @classmethod
    def clear(cls) -> None:
        """Discard the current authentication; a no-op when unset."""
        _current.set(None)

    @classmethod
    def require_authentication(cls) -> Authentication:
        """Return the current authentication; raise when absent/anonymous.

        Anonymous tokens (``authenticated=False``) count as absent —
        :class:`AuthenticationError` carries 401 semantics.
        """
        auth = _current.get()
        if auth is None or not auth.authenticated:
            raise AuthenticationError(
                "No authenticated principal in the current security context"
            )
        return auth

    @classmethod
    def with_authentication(
        cls, authentication: Authentication
    ) -> _BoundAuthentication:
        """Set + restore scope around a block or function.

        ::

            with SecurityContextHolder.with_authentication(auth):
                svc.call()            # sees auth

            @SecurityContextHolder.with_authentication(auth)
            def work(): ...           # sees auth per call
        """
        return _BoundAuthentication(authentication)


__all__ = ["SecurityContextHolder"]
