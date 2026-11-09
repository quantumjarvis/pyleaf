"""JWT bearer-token authentication provider (WI 3.8, PYL-39).

``JwtAuthenticationProvider`` verifies ``Authorization: Bearer <jwt>``
credentials with pyjwt and maps the claims to an ``Authentication``:

- **signature** — HS256 with ``secret``, RS256 with ``public_key`` (PEM);
  exactly one of the two is required.
- **``exp`` is REQUIRED** — a missing or expired ``exp`` claim is an
  invalid token.
- **``iss``/``aud``** are verified only when ``issuer``/``audience`` are
  configured (an ``aud`` claim in a token is otherwise ignored).
- **principal** — ``sub``, falling back to ``preferred_username``, then
  the literal ``"jwt-user"``. All claims are exposed verbatim as
  ``principal.details``.
- **authorities** —
  ``roles`` (list or space/comma-separated string) ->
  ``ROLE_<value>`` via :func:`pyleaf.security.role` (prefix normalized);
  ``authorities`` (list or string) -> verbatim ``GrantedAuthority``;
  ``scope``/``scp`` (space-separated string or list) ->
  ``SCOPE_<value>``.

Every failure — bad signature, expired, wrong issuer/audience, malformed
token, wrong key type — raises ``AuthenticationError`` (the 401 path);
the provider never propagates an unexpected exception as a 500.

Configuration properties (read by ``SecurityAutoConfiguration``):

- ``pyleaf.security.jwt.secret`` — HS256 shared secret;
- ``pyleaf.security.jwt.public-key`` — RS256 public key in PEM form
  (requires pyjwt's crypto support, i.e. ``cryptography`` installed);
- ``pyleaf.security.jwt.issuer`` / ``pyleaf.security.jwt.audience`` —
  optional claim verification.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pyleaf.core.errors import PyleafError

from .core import Authentication, GrantedAuthority, Principal, role
from .errors import AuthenticationError

# A property-name constant, not a credential — S105 does not apply.
JWT_SECRET_PROPERTY = "pyleaf.security.jwt.secret"  # noqa: S105
JWT_PUBLIC_KEY_PROPERTY = "pyleaf.security.jwt.public-key"
JWT_ISSUER_PROPERTY = "pyleaf.security.jwt.issuer"
JWT_AUDIENCE_PROPERTY = "pyleaf.security.jwt.audience"

_SCOPE_PREFIX = "SCOPE_"


def _pyjwt() -> Any:
    """Import pyjwt lazily; raise ``PyleafError`` when it is absent."""
    try:
        import jwt
    except ImportError as exc:
        raise PyleafError(
            "JwtAuthenticationProvider requires the 'pyjwt' package"
            " — pip install pyjwt"
        ) from exc
    return jwt


def _str_list(value: Any) -> list[str]:
    """Normalize a claim to a string list: split strings, iterate lists."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in value.replace(",", " ").split() if part]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
    return [str(value)]


def authentication_from_claims(claims: Mapping[str, Any]) -> Authentication:
    """Map a decoded JWT claim set to an ``Authentication``.

    See the module docstring for the claims->principal/authorities
    mapping. The token that produced the claims is never stored —
    ``Authentication.credentials`` stays ``None``.
    """
    name = claims.get("sub") or claims.get("preferred_username") or "jwt-user"
    authorities: set[GrantedAuthority] = set()
    for value in _str_list(claims.get("roles")):
        authorities.add(role(value))
    for value in _str_list(claims.get("authorities")):
        authorities.add(GrantedAuthority(value))
    for value in _str_list(claims.get("scope")) + _str_list(claims.get("scp")):
        authorities.add(GrantedAuthority(_SCOPE_PREFIX + value))
    return Authentication(
        principal=Principal(str(name), details=dict(claims)),
        authorities=frozenset(authorities),
    )


class JwtAuthenticationProvider:
    """Verify ``Bearer`` JWT credentials and produce ``Authentication``.

    Exactly one of ``secret`` (HS256) or ``public_key`` (RS256 PEM) is
    required — mixing symmetric and asymmetric verification in one
    provider is deliberately unsupported.
    """

    def __init__(
        self,
        *,
        secret: str | None = None,
        public_key: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
        leeway: float = 0.0,
    ) -> None:
        if (secret is None) == (public_key is None):
            raise PyleafError(
                "JwtAuthenticationProvider requires exactly one of"
                " 'secret' (HS256) or 'public_key' (RS256)"
            )
        self._key = secret if secret is not None else public_key
        self._algorithms = ["HS256"] if secret is not None else ["RS256"]
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway

    def authenticate(self, credentials: str) -> Authentication:
        """Decode + verify ``credentials``; raise ``AuthenticationError``.

        ``exp`` is required and verified; ``iss``/``aud`` are verified
        only when configured. Every pyjwt failure surfaces as
        ``AuthenticationError`` — callers map it to 401.
        """
        jwt = _pyjwt()
        options: dict[str, Any] = {
            "require": ["exp"],
            "verify_signature": True,
            "verify_aud": self._audience is not None,
            "verify_iss": self._issuer is not None,
        }
        kwargs: dict[str, Any] = {"leeway": self._leeway}
        if self._issuer is not None:
            kwargs["issuer"] = self._issuer
        if self._audience is not None:
            kwargs["audience"] = self._audience
        try:
            claims: dict[str, Any] = jwt.decode(
                credentials,
                self._key,
                algorithms=self._algorithms,
                options=options,
                **kwargs,
            )
        except Exception as exc:
            # Bad signature, expired, wrong iss/aud, malformed token,
            # wrong key type — all the same 401 path, never a 500.
            raise AuthenticationError(f"Invalid bearer token: {exc}") from exc
        return authentication_from_claims(claims)


__all__ = [
    "JWT_AUDIENCE_PROPERTY",
    "JWT_ISSUER_PROPERTY",
    "JWT_PUBLIC_KEY_PROPERTY",
    "JWT_SECRET_PROPERTY",
    "JwtAuthenticationProvider",
    "authentication_from_claims",
]
