"""Security domain types: Principal, GrantedAuthority, Authentication.

Role convention (Spring-compatible): a role named ``X`` is stored as the
authority string ``ROLE_X``. :func:`role` applies the prefix and
:meth:`Authentication.has_role` normalizes it, so ``has_role("ADMIN")``
and ``has_role("ROLE_ADMIN")`` both match a ``ROLE_ADMIN`` authority.

Credentials are NEVER stored: :class:`Authentication` does not accept
them at construction and ``credentials`` reads ``None``, mirroring
Spring's post-authentication ``erase_credentials`` behaviour — a token or
password that produced the authentication cannot leak through the
security context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Authority-string prefix marking a role (Spring convention).
ROLE_PREFIX = "ROLE_"


@dataclass(frozen=True)
class GrantedAuthority:
    """One granted authority; role strings carry the ``ROLE_`` prefix."""

    authority: str


def role(name: str) -> GrantedAuthority:
    """Build a role authority: ``role("ADMIN")`` -> ``ROLE_ADMIN``.

    Idempotent — an already-prefixed name is used as-is.
    """
    if name.startswith(ROLE_PREFIX):
        return GrantedAuthority(name)
    return GrantedAuthority(ROLE_PREFIX + name)


@dataclass(frozen=True)
class Principal:
    """The authenticated party: a name plus free-form ``details``."""

    name: str
    details: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Authentication:
    """An authenticated (or anonymous) principal and its authorities.

    ``authenticated=False`` models an anonymous token: it is present in
    the context but counts as "no auth" for 401/403 decisions, matching
    :meth:`SecurityContextHolder.require_authentication` and the method
    interceptor's deny path. ``credentials`` is always ``None`` — see the
    module docstring.
    """

    principal: Principal
    authorities: frozenset[GrantedAuthority] = frozenset()
    authenticated: bool = True
    credentials: Any = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Accept any iterable of GrantedAuthority; normalize to frozenset.
        object.__setattr__(self, "authorities", frozenset(self.authorities))

    def has_role(self, name: str) -> bool:
        """True when a ``ROLE_<name>`` authority is present."""
        return role(name) in self.authorities

    def has_authority(self, authority: str) -> bool:
        """True when the exact authority string is present."""
        return GrantedAuthority(authority) in self.authorities


__all__ = [
    "ROLE_PREFIX",
    "Authentication",
    "GrantedAuthority",
    "Principal",
    "role",
]
