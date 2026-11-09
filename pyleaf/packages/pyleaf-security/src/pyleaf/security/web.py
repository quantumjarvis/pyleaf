"""Web security: ``SecurityMiddleware`` + ordered path rules (WI 3.8, PYL-39).

``SecurityMiddleware`` is a ``@WebMiddleware``-compatible bean (mounted by
``pyleaf.web.register_middleware``). On each request it

1. authenticates the ``Authorization: Bearer <token>`` header through the
   ordered ``AuthenticationProvider`` beans (``ctx.get_all`` @Order) —
   ``JwtAuthenticationProvider`` first when configured. Non-Bearer
   schemes and a bare ``Bearer`` with no token count as anonymous;
   providers that return ``None`` pass the token to the next provider;
   an ``AuthenticationError`` from any provider -> 401 IMMEDIATELY
   (a request claiming credentials it cannot prove never downgrades
   silently to anonymous — pinned).
2. authorizes the path (below) -> 401/403 short-circuit.
3. binds the ``Authentication`` to ``SecurityContextHolder`` for the
   request scope (an anonymous ``authenticated=False`` token when no
   credentials), calls downstream, then resets the holder. The contextvar
   is set BEFORE ``call_next`` so route handlers — sync or async — see
   the principal (anyio propagates the context into the downstream task
   and worker threads).
4. maps unhandled ``AuthenticationError``/``AccessDeniedError`` raised by
   downstream handlers to 401/403 when no ``@ControllerAdvice`` claimed
   them first (the advice dispatcher sits INSIDE bean middleware, so
   user advice always wins).

Path rules
----------
``pyleaf.security.rules`` — an ordered list of mappings::

    pyleaf:
      security:
        rules:
          - path: /api/public/**
            access: permit_all
          - path: /api/admin/**
            roles: [ADMIN]
          - path: /api/single/*        # one segment only
            access: authenticated

Patterns are Ant-style: ``**`` = zero or more path SEGMENTS (a trailing
``/**`` also matches the bare prefix), ``*`` = within one segment,
``?`` = one non-``/`` char. FIRST match wins — unlike Spring's
anyRequest-last model, ordering is the user's responsibility; put
specific rules first. ``roles`` entries need no ``ROLE_`` prefix
(normalized by ``Authentication.has_role``). A rule with only ``path``
requires authentication. Both ``roles`` and ``access`` in one rule, a
missing ``path`` or an unknown ``access`` -> ``PyleafError`` at startup.

Evaluation order: user rules (in declared order) -> built-in actuator
rule -> ``pyleaf.security.default-access`` (``authenticated`` default,
or ``permit_all``). Secure-all-by-default mirrors Spring Boot: with
security on the classpath and no rules, EVERYTHING — including
``/health`` — requires authentication; add an explicit
``access: permit_all`` rule for public paths.

Actuator: when ``pyleaf.actuator`` is importable, a default rule
``<pyleaf.actuator.base-path>/**`` -> role ``ACTUATOR`` is appended AFTER
the user rules, so a user rule on an actuator path always wins.
``pyleaf.security.actuator.enabled=false`` removes the rule entirely —
the paths then fall through to ``default-access``.

Error shape: 401/403 responses reuse the advice ``{"detail": ...}`` JSON
shape (FastAPI-compatible); 401s also carry ``WWW-Authenticate: Bearer``.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pyleaf.core import ApplicationContext, Environment, Order
from pyleaf.core.errors import PyleafError

from .context import SecurityContextHolder
from .core import Authentication, Principal
from .errors import AccessDeniedError, AuthenticationError

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

try:
    from starlette.responses import JSONResponse
except ImportError:  # pragma: no cover - requires a web-less env
    # Placeholder keeps the module importable without starlette; dispatch
    # (the only call site) is never invoked then.
    class JSONResponse:  # type: ignore[no-redef]
        """Placeholder for ``starlette.responses.JSONResponse``."""


try:
    from pyleaf.web import WebMiddleware as _mark_middleware
except ImportError:  # pragma: no cover - requires a web-less env
    # pyleaf-web absent: register_middleware only exists with pyleaf.web,
    # so the marker is set directly for symmetry instead of importing it.
    def _mark_middleware(cls: type[Any]) -> type[Any]:  # type: ignore[no-redef]
        """Fallback ``@WebMiddleware``: set the marker attr only."""
        cls.__pyleaf_middleware__ = True
        return cls


RULES_PROPERTY = "pyleaf.security.rules"
DEFAULT_ACCESS_PROPERTY = "pyleaf.security.default-access"
ACTUATOR_SECURITY_PROPERTY = "pyleaf.security.actuator.enabled"
ACTUATOR_BASE_PATH_PROPERTY = "pyleaf.actuator.base-path"
ACTUATOR_DEFAULT_BASE_PATH = "/actuator"

#: ``default-access`` fallback when the property is unset — secure all.
DEFAULT_ACCESS = "authenticated"

_ACCESSES = frozenset({"permit_all", "authenticated", "deny_all"})

#: ``@Order`` on the middleware class — honored only if the class is
#: registered as a component; the auto-configuration's ``@Bean`` def
#: carries the default order (unordered -> innermost bean middleware).
SECURITY_WEB_ORDER = -100


@runtime_checkable
class AuthenticationProvider(Protocol):
    """Bearer-credential -> ``Authentication`` contract.

    Providers run in ``ctx.get_all`` order (@Order, then name). Return
    ``None`` when the credential is not this provider's shape (the next
    provider tries it); raise ``AuthenticationError`` when it IS the
    provider's scheme but fails verification -> 401.
    """

    def authenticate(self, credentials: str) -> Authentication | None:
        """Authenticate ``credentials`` or return ``None`` to pass on."""
        ...


def _compile_ant(pattern: str) -> re.Pattern[str]:
    """Compile an Ant-style path pattern to an anchored regex.

    ``**`` as a whole segment matches zero or more segments — a trailing
    ``/**`` also matches the bare prefix (``/a/**`` -> ``/a``), and
    ``/**/`` collapses (``/a/**/b`` -> ``/a/b``). ``*``/``?`` stay within
    one segment. ``**`` NOT on a segment boundary is treated as ``.*``.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*" and i + 1 < n and pattern[i + 1] == "*":
            j = i + 2
            bounded = (i == 0 or pattern[i - 1] == "/") and (
                j == n or pattern[j] == "/"
            )
            if bounded:
                # Whole-segment '**': zero or more segments. Swallow a
                # previously emitted '/' so 'x/**' also matches 'x' and
                # 'x/**/y' also matches 'x/y'.
                if out and out[-1] == "/":
                    out.pop()
                out.append("(?:/[^/]*)*")
            else:
                out.append(".*")
            i = j
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


class PathRule:
    """One ordered access rule: an Ant pattern + an access decision.

    ``access`` is ``permit_all`` | ``authenticated`` | ``deny_all`` |
    ``roles``; ``roles`` lists the accepted role names (``ROLE_`` prefix
    optional — ``Authentication.has_role`` normalizes).
    """

    def __init__(
        self,
        pattern: str,
        access: str = "authenticated",
        roles: Iterable[str] = (),
    ) -> None:
        if access != "roles" and access not in _ACCESSES:
            raise PyleafError(
                f"Unknown access '{access}' for path rule '{pattern}'"
            )
        self.pattern = pattern
        self.access = access
        self.roles = tuple(roles)
        self._regex = _compile_ant(pattern)

    def matches(self, path: str) -> bool:
        """True when ``path`` matches this rule's pattern."""
        return self._regex.match(path) is not None

    def __repr__(self) -> str:
        return f"PathRule({self.pattern!r}, access={self.access!r}, roles={self.roles!r})"


def parse_rules(value: Any) -> list[PathRule]:
    """Normalize the ``pyleaf.security.rules`` property into ``PathRule``s.

    Each entry is a mapping ``{"path": <ant-pattern>}`` plus EITHER
    ``"roles": [..]`` (list or single string) OR
    ``"access": "permit_all"|"authenticated"|"deny_all"``. An entry with
    only ``path`` requires authentication. Malformed entries raise
    ``PyleafError`` — config errors fail fast at bean creation, not at
    request time.
    """
    if value is None:
        return []
    if isinstance(value, (str, Mapping)) or not isinstance(value, Iterable):
        raise PyleafError(
            f"'{RULES_PROPERTY}' must be a list of rule mappings"
        )
    rules: list[PathRule] = []
    for index, item in enumerate(value):
        where = f"{RULES_PROPERTY}[{index}]"
        if not isinstance(item, Mapping):
            raise PyleafError(f"'{where}' must be a mapping, got {item!r}")
        pattern = item.get("path")
        if not isinstance(pattern, str) or not pattern:
            raise PyleafError(f"'{where}.path' must be a non-empty string")
        roles_value = item.get("roles")
        access_value = item.get("access")
        if roles_value is not None and access_value is not None:
            raise PyleafError(
                f"'{where}' sets both 'roles' and 'access' — pick one"
            )
        if roles_value is not None:
            role_list = (
                [roles_value]
                if isinstance(roles_value, str)
                else [str(name) for name in roles_value]
            )
            rules.append(PathRule(pattern, "roles", role_list))
        elif access_value is not None:
            rules.append(PathRule(pattern, str(access_value)))
        else:
            rules.append(PathRule(pattern, "authenticated"))
    return rules


def _module_present(name: str) -> bool:
    """True when ``name`` resolves via ``find_spec`` (never executes it)."""
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _actuator_default_rule(env: Environment) -> PathRule | None:
    """The ``/actuator/**`` -> ACTUATOR-role rule, or ``None`` when off.

    Present only when ``pyleaf.actuator`` is importable AND
    ``pyleaf.security.actuator.enabled`` is not false; honors
    ``pyleaf.actuator.base-path``.
    """
    if not env.get(ACTUATOR_SECURITY_PROPERTY, bool, True):
        return None
    if not _module_present("pyleaf.actuator"):
        return None
    base = str(
        env.get(ACTUATOR_BASE_PATH_PROPERTY) or ACTUATOR_DEFAULT_BASE_PATH
    ).rstrip("/")
    if not base:
        base = ACTUATOR_DEFAULT_BASE_PATH
    return PathRule(f"{base}/**", access="roles", roles=("ACTUATOR",))


def _anonymous() -> Authentication:
    """The anonymous token bound to credential-less requests."""
    return Authentication(
        principal=Principal("anonymousUser"), authenticated=False
    )


def _problem_response(status: int, detail: str) -> Response:
    """A 401/403 JSON body in the advice ``{"detail": ...}`` shape."""
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


@_mark_middleware
@Order(SECURITY_WEB_ORDER)
class SecurityMiddleware:
    """Authenticate + authorize every request; bind the security context.

    See the module docstring for the rules model, ordering and error
    shape. Constructed with the ``ApplicationContext`` (like the
    actuator's ``ActuatorCustomizer``); providers resolve lazily on first
    request via ``ctx.get_all(AuthenticationProvider)``.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx
        env = ctx.environment
        self._default_access = str(
            env.get(DEFAULT_ACCESS_PROPERTY) or DEFAULT_ACCESS
        )
        if self._default_access not in ("authenticated", "permit_all"):
            raise PyleafError(
                f"'{DEFAULT_ACCESS_PROPERTY}' must be 'authenticated' or"
                f" 'permit_all', got {self._default_access!r}"
            )
        self._rules = parse_rules(env.get(RULES_PROPERTY))
        actuator_rule = _actuator_default_rule(env)
        if actuator_rule is not None:
            self._rules.append(actuator_rule)
        self._providers: list[AuthenticationProvider] | None = None

    @property
    def rules(self) -> tuple[PathRule, ...]:
        """The effective ordered rules (user rules, then actuator default)."""
        return tuple(self._rules)

    def _provider_list(self) -> list[AuthenticationProvider]:
        if self._providers is None:
            self._providers = list(self._ctx.get_all(AuthenticationProvider))
        return self._providers

    def _authenticate(self, request: Request) -> Authentication | None:
        """Run providers on a Bearer credential; ``None`` when anonymous."""
        header = request.headers.get("authorization")
        if header is None:
            return None
        scheme, _, credentials = header.partition(" ")
        if scheme.lower() != "bearer":
            return None
        credentials = credentials.strip()
        if not credentials:
            return None
        for provider in self._provider_list():
            authentication = provider.authenticate(credentials)
            if authentication is not None:
                return authentication
        return None

    def _authorize(self, auth: Authentication, path: str) -> Response | None:
        """First-match rule evaluation; a response means "denied"."""
        rule = next((r for r in self._rules if r.matches(path)), None)
        if rule is None:
            if self._default_access == "permit_all":
                return None
            if auth.authenticated:
                return None
            return _problem_response(401, "Authentication required")
        if rule.access == "permit_all":
            return None
        if rule.access == "deny_all":
            return _problem_response(403, "Access denied")
        if not auth.authenticated:
            return _problem_response(401, "Authentication required")
        if rule.access == "authenticated":
            return None
        if any(auth.has_role(name) for name in rule.roles):
            return None
        return _problem_response(403, "Access denied")

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Authenticate, authorize, bind the context, call downstream."""
        try:
            authentication = self._authenticate(request)
        except AuthenticationError as exc:
            return _problem_response(401, str(exc) or "Authentication failed")
        auth = authentication if authentication is not None else _anonymous()
        denied = self._authorize(auth, request.url.path)
        if denied is not None:
            return denied
        token = SecurityContextHolder.set_authentication(auth)
        try:
            return await call_next(request)
        except AuthenticationError as exc:
            # Handler-raised auth errors the advice layer did not claim.
            return _problem_response(401, str(exc) or "Authentication required")
        except AccessDeniedError as exc:
            return _problem_response(403, str(exc) or "Access denied")
        finally:
            SecurityContextHolder.reset(token)


__all__ = [
    "ACTUATOR_BASE_PATH_PROPERTY",
    "ACTUATOR_DEFAULT_BASE_PATH",
    "ACTUATOR_SECURITY_PROPERTY",
    "DEFAULT_ACCESS",
    "DEFAULT_ACCESS_PROPERTY",
    "RULES_PROPERTY",
    "SECURITY_WEB_ORDER",
    "AuthenticationProvider",
    "PathRule",
    "SecurityMiddleware",
    "parse_rules",
]
