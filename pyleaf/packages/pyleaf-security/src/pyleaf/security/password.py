"""PasswordEncoder contract and encoders (WI 3.8, PYL-39).

Mirrors Spring's ``PasswordEncoder`` family. Stored hashes use the
delegating ``{id}hash`` format — ``{argon2}$argon2id$...``,
``{bcrypt}$2b$...``, ``{noop}plaintext`` — so the algorithm travels with
the hash and :class:`DelegatingPasswordEncoder` can verify credentials
produced by older encoders and flag them for upgrade.

Encoder semantics (pinned):

- ``encode`` produces a ``{id}``-prefixed hash on the delegating encoder,
  a raw hash on the concrete encoders.
- ``matches`` NEVER raises: malformed hashes, unknown ``{id}`` prefixes
  and verification failures all return ``False``.
- ``{noop}`` hashes are REJECTED by :class:`DelegatingPasswordEncoder`
  unless ``allow_noop=True`` — plaintext verification is opt-in, and
  ``NoOpPasswordEncoder`` is never the default encoder.
- ``needs_upgrade`` is true when the stored ``{id}`` differs from the
  default id, when the concrete encoder's parameters drifted (bcrypt
  cost, argon2 rehash policy), or when the hash is un-prefixed/malformed.
- :meth:`DelegatingPasswordEncoder.upgrade_if_needed` implements
  upgrade-on-login: verify, then re-encode when stale.

The optional hasher libraries are probed lazily: ``argon2`` (the
``argon2-cffi`` distribution) is the preferred default, ``bcrypt`` the
fallback. Constructing an encoder whose library is absent raises
``PyleafError`` — the auto-configuration gates the bean on
``OnClass``-style module probes instead.
"""

from __future__ import annotations

import hmac
import importlib.util
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pyleaf.core.errors import PyleafError


def _importable(module: str) -> bool:
    """True when ``module`` resolves via ``find_spec`` (never executes it)."""
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


@runtime_checkable
class PasswordEncoder(Protocol):
    """The encode/verify contract every password encoder implements."""

    def encode(self, raw_password: str) -> str:
        """Hash ``raw_password`` for storage."""
        ...

    def matches(self, raw_password: str, encoded_password: str) -> bool:
        """True when ``raw_password`` verifies against ``encoded_password``."""
        ...

    def needs_upgrade(self, encoded_password: str) -> bool:
        """True when ``encoded_password`` should be re-encoded on next login."""
        ...


class NoOpPasswordEncoder:
    """Plaintext "encoder" — for tests only, NEVER a production default."""

    def encode(self, raw_password: str) -> str:
        return raw_password

    def matches(self, raw_password: str, encoded_password: str) -> bool:
        try:
            return bool(hmac.compare_digest(raw_password, encoded_password))
        except (TypeError, ValueError):
            return False

    def needs_upgrade(self, encoded_password: str) -> bool:
        return False


class BCryptPasswordEncoder:
    """BCrypt encoder — raw ``$2b$...`` hashes, no ``{id}`` prefix.

    Requires the ``bcrypt`` package (OnClass gate); the constructor raises
    ``PyleafError`` when it is absent. ``rounds`` is the bcrypt cost
    factor (default 10); ``needs_upgrade`` compares the hash's encoded
    cost to it.
    """

    def __init__(self, rounds: int = 10) -> None:
        if not _importable("bcrypt"):
            raise PyleafError(
                "BCryptPasswordEncoder requires the 'bcrypt' package"
                " — pip install bcrypt"
            )
        self._rounds = rounds

    def encode(self, raw_password: str) -> str:
        import bcrypt

        hashed = bcrypt.hashpw(
            raw_password.encode("utf-8"), bcrypt.gensalt(self._rounds)
        )
        return hashed.decode("ascii")

    def matches(self, raw_password: str, encoded_password: str) -> bool:
        import bcrypt

        try:
            return bool(
                bcrypt.checkpw(
                    raw_password.encode("utf-8"),
                    encoded_password.encode("ascii"),
                )
            )
        except (ValueError, TypeError):
            # Malformed hash (bad version/cost/length) -> no match.
            return False

    def needs_upgrade(self, encoded_password: str) -> bool:
        try:
            cost = int(encoded_password.split("$")[2])
        except (IndexError, ValueError):
            return True
        return cost != self._rounds


class Argon2PasswordEncoder:
    """Argon2id encoder — raw PHC ``$argon2id$...`` hashes.

    Requires ``argon2-cffi`` (OnClass gate); the constructor raises
    ``PyleafError`` when it is absent. Keyword arguments pass through to
    ``argon2.PasswordHasher`` — e.g. ``time_cost``, ``memory_cost``,
    ``parallelism`` — and parameter changes mark old hashes via
    ``check_needs_rehash``.
    """

    def __init__(self, **hasher_params: Any) -> None:
        if not _importable("argon2"):
            raise PyleafError(
                "Argon2PasswordEncoder requires the 'argon2-cffi' package"
                " — pip install argon2-cffi"
            )
        from argon2 import PasswordHasher

        self._hasher = PasswordHasher(**hasher_params)

    def encode(self, raw_password: str) -> str:
        return self._hasher.hash(raw_password)

    def matches(self, raw_password: str, encoded_password: str) -> bool:
        try:
            return bool(self._hasher.verify(encoded_password, raw_password))
        except Exception:
            # VerifyMismatchError on mismatch, Argon2Error on malformed
            # hashes — both mean "does not match".
            return False

    def needs_upgrade(self, encoded_password: str) -> bool:
        try:
            return bool(self._hasher.check_needs_rehash(encoded_password))
        except Exception:
            return True


class DelegatingPasswordEncoder:
    """Spring-style ``{id}hash`` delegating encoder.

    ``encode`` produces ``{<default_id>}<hash>``; ``matches`` parses the
    ``{id}`` prefix and delegates to the matching encoder. An ``{id}``
    with no registered encoder — including ``{noop}`` when ``allow_noop``
    is false — and un-prefixed hashes match as ``False``.
    """

    def __init__(
        self,
        default_id: str,
        encoders: Mapping[str, PasswordEncoder],
        *,
        allow_noop: bool = False,
    ) -> None:
        self._default_id = default_id
        self._encoders: dict[str, PasswordEncoder] = dict(encoders)
        if allow_noop:
            self._encoders.setdefault("noop", NoOpPasswordEncoder())
        if default_id not in self._encoders:
            raise PyleafError(
                f"DelegatingPasswordEncoder default id '{default_id}'"
                " has no registered encoder"
            )

    @property
    def default_id(self) -> str:
        """The ``{id}`` produced by :meth:`encode`."""
        return self._default_id

    @staticmethod
    def _split(encoded_password: str) -> tuple[str | None, str]:
        """Split ``{id}hash`` -> ``(id, hash)``; ``(None, raw)`` when absent."""
        if not encoded_password.startswith("{"):
            return None, encoded_password
        closing = encoded_password.find("}", 1)
        if closing < 0:
            return None, encoded_password
        return encoded_password[1:closing], encoded_password[closing + 1 :]

    def encode(self, raw_password: str) -> str:
        return (
            "{"
            + self._default_id
            + "}"
            + self._encoders[self._default_id].encode(raw_password)
        )

    def matches(self, raw_password: str, encoded_password: str) -> bool:
        encoder_id, hashed = self._split(encoded_password)
        if encoder_id is None:
            return False
        encoder = self._encoders.get(encoder_id)
        if encoder is None:
            return False
        try:
            return bool(encoder.matches(raw_password, hashed))
        except Exception:
            return False

    def needs_upgrade(self, encoded_password: str) -> bool:
        encoder_id, hashed = self._split(encoded_password)
        if encoder_id is None:
            # Un-prefixed legacy hash — always flagged for migration.
            return True
        if encoder_id != self._default_id:
            return True
        encoder = self._encoders.get(encoder_id)
        if encoder is None:
            return True
        try:
            return bool(encoder.needs_upgrade(hashed))
        except Exception:
            return True

    def upgrade_if_needed(
        self, raw_password: str, encoded_password: str
    ) -> str | None:
        """Upgrade-on-login: re-encode when the hash verifies AND is stale.

        Returns a fresh ``encode()`` result to persist, or ``None`` when
        the stored hash is already current (or does not verify — never
        upgrade an unverified password).
        """
        if self.matches(raw_password, encoded_password) and self.needs_upgrade(
            encoded_password
        ):
            return self.encode(raw_password)
        return None


def build_default_encoder(
    *, allow_noop: bool = False
) -> DelegatingPasswordEncoder:
    """Build the default :class:`DelegatingPasswordEncoder`.

    Registers every installed hasher — ``{argon2}`` and/or ``{bcrypt}``
    decode — with ``argon2`` as the default id when available, else
    ``bcrypt``. ``{noop}`` is only registered under ``allow_noop=True``
    and is NEVER the default. Raises ``PyleafError`` when no hasher
    library is installed.
    """
    encoders: dict[str, PasswordEncoder] = {}
    if _importable("argon2"):
        encoders["argon2"] = Argon2PasswordEncoder()
    if _importable("bcrypt"):
        encoders["bcrypt"] = BCryptPasswordEncoder()
    if not encoders:
        raise PyleafError(
            "No password hasher installed — install argon2-cffi or bcrypt"
        )
    default_id = "argon2" if "argon2" in encoders else "bcrypt"
    return DelegatingPasswordEncoder(default_id, encoders, allow_noop=allow_noop)


__all__ = [
    "Argon2PasswordEncoder",
    "BCryptPasswordEncoder",
    "DelegatingPasswordEncoder",
    "NoOpPasswordEncoder",
    "PasswordEncoder",
    "build_default_encoder",
]
