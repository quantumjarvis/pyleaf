"""Security core for pyleaf (WI 3.7, PYL-38).

Domain types (``Authentication``/``Principal``/``GrantedAuthority``), the
contextvar-backed ``SecurityContextHolder``, the safe whitelist
expression interpreter (``ParsedExpression`` — no dynamic code anywhere),
the ``@PreAuthorize``/``@PostAuthorize``/``@Secured`` markers and their
``MethodSecurityInterceptor`` aspect (@Order(50): outside the tx
interceptor, so denied calls never open a transaction), wired by the
``pyleaf.autoconfigure`` entry point. Web/JWT integration is 3.8.
"""

from .autoconfig import SecurityAutoConfiguration
from .context import SecurityContextHolder
from .core import (
    ROLE_PREFIX,
    Authentication,
    GrantedAuthority,
    Principal,
    role,
)
from .errors import AccessDeniedError, AuthenticationError
from .expression import ParsedExpression, SecurityExpressionError
from .method import (
    DEFAULT_SECURITY_ORDER,
    POST_ATTR,
    PRE_ATTR,
    SECURED_ATTR,
    SECURITY_ORDER_PROPERTY,
    MethodSecurityInterceptor,
    PostAuthorize,
    PreAuthorize,
    Secured,
    SecurityMeta,
    security_meta,
)

__all__ = [
    "DEFAULT_SECURITY_ORDER",
    "POST_ATTR",
    "PRE_ATTR",
    "ROLE_PREFIX",
    "SECURED_ATTR",
    "SECURITY_ORDER_PROPERTY",
    "AccessDeniedError",
    "Authentication",
    "AuthenticationError",
    "GrantedAuthority",
    "MethodSecurityInterceptor",
    "ParsedExpression",
    "PostAuthorize",
    "PreAuthorize",
    "Principal",
    "Secured",
    "SecurityAutoConfiguration",
    "SecurityContextHolder",
    "SecurityExpressionError",
    "SecurityMeta",
    "role",
    "security_meta",
]
