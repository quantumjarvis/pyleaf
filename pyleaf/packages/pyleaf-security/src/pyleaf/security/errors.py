"""Exception hierarchy for pyleaf-security.

The two error types carry HTTP-adjacent semantics for the web layer
(work item 3.8 maps them): ``AuthenticationError`` is the 401 case —
no usable authentication in the context; ``AccessDeniedError`` is the
403 case — authenticated, but the check denied the call.
"""

from __future__ import annotations

from pyleaf.core.errors import PyleafError


class AuthenticationError(PyleafError):
    """No authentication is present (or it is anonymous) — 401 semantics."""


class AccessDeniedError(PyleafError):
    """Authentication is present but access was denied — 403 semantics."""


__all__ = ["AccessDeniedError", "AuthenticationError"]
