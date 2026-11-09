"""Security for pyleaf (WI 3.7 PYL-38, WI 3.8 PYL-39).

Domain types (``Authentication``/``Principal``/``GrantedAuthority``), the
contextvar-backed ``SecurityContextHolder``, the safe whitelist
expression interpreter (``ParsedExpression`` — no dynamic code anywhere),
the ``@PreAuthorize``/``@PostAuthorize``/``@Secured`` markers and their
``MethodSecurityInterceptor`` aspect (@Order(50): outside the tx
interceptor, so denied calls never open a transaction), wired by the
``pyleaf.autoconfigure`` entry point.

Web layer (3.8): ``SecurityMiddleware`` (a ``@WebMiddleware`` bean that
authenticates ``Authorization: Bearer`` through ordered
``AuthenticationProvider`` beans — ``JwtAuthenticationProvider`` for
HS256/RS256 JWTs — binds ``SecurityContextHolder`` per request, and
enforces first-match Ant-style ``pyleaf.security.rules`` plus the
``/actuator/**`` -> ``ACTUATOR`` default), and the ``PasswordEncoder``
family with Spring-style ``{id}hash`` delegating format. Optional deps
(``pyjwt``, ``argon2-cffi``, ``bcrypt``, ``pyleaf-web``) are probed via
OnClass-style gates; the package imports fine without them.
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
from .jwt import (
    JWT_AUDIENCE_PROPERTY,
    JWT_ISSUER_PROPERTY,
    JWT_PUBLIC_KEY_PROPERTY,
    JWT_SECRET_PROPERTY,
    JwtAuthenticationProvider,
    authentication_from_claims,
)
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
from .password import (
    Argon2PasswordEncoder,
    BCryptPasswordEncoder,
    DelegatingPasswordEncoder,
    NoOpPasswordEncoder,
    PasswordEncoder,
    build_default_encoder,
)
from .web import (
    ACTUATOR_BASE_PATH_PROPERTY,
    ACTUATOR_DEFAULT_BASE_PATH,
    ACTUATOR_SECURITY_PROPERTY,
    DEFAULT_ACCESS,
    DEFAULT_ACCESS_PROPERTY,
    RULES_PROPERTY,
    SECURITY_WEB_ORDER,
    AuthenticationProvider,
    PathRule,
    SecurityMiddleware,
    parse_rules,
)

__all__ = [
    "ACTUATOR_BASE_PATH_PROPERTY",
    "ACTUATOR_DEFAULT_BASE_PATH",
    "ACTUATOR_SECURITY_PROPERTY",
    "DEFAULT_ACCESS",
    "DEFAULT_ACCESS_PROPERTY",
    "DEFAULT_SECURITY_ORDER",
    "JWT_AUDIENCE_PROPERTY",
    "JWT_ISSUER_PROPERTY",
    "JWT_PUBLIC_KEY_PROPERTY",
    "JWT_SECRET_PROPERTY",
    "POST_ATTR",
    "PRE_ATTR",
    "ROLE_PREFIX",
    "RULES_PROPERTY",
    "SECURED_ATTR",
    "SECURITY_ORDER_PROPERTY",
    "SECURITY_WEB_ORDER",
    "AccessDeniedError",
    "Argon2PasswordEncoder",
    "Authentication",
    "AuthenticationError",
    "AuthenticationProvider",
    "BCryptPasswordEncoder",
    "DelegatingPasswordEncoder",
    "GrantedAuthority",
    "JwtAuthenticationProvider",
    "MethodSecurityInterceptor",
    "NoOpPasswordEncoder",
    "ParsedExpression",
    "PasswordEncoder",
    "PathRule",
    "PostAuthorize",
    "PreAuthorize",
    "Principal",
    "Secured",
    "SecurityAutoConfiguration",
    "SecurityContextHolder",
    "SecurityExpressionError",
    "SecurityMeta",
    "SecurityMiddleware",
    "authentication_from_claims",
    "build_default_encoder",
    "parse_rules",
    "role",
    "security_meta",
]
