"""PasswordEncoder edge cases: missing-library gates, malformed-hash
fallbacks and the delegating encoder's defensive branches (PYL-42).

The happy paths live in ``test_password_encoder.py``; these tests pin the
contract that ``matches``/``needs_upgrade`` NEVER raise — every malformed
input degrades to ``False``/``True``-for-upgrade — and that constructing
an encoder whose hasher library is absent fails fast with
:class:`PyleafError`.
"""

from __future__ import annotations

import pytest
from pyleaf.core.errors import PyleafError
from pyleaf.security import (
    Argon2PasswordEncoder,
    BCryptPasswordEncoder,
    DelegatingPasswordEncoder,
    NoOpPasswordEncoder,
    build_default_encoder,
)
from pyleaf.security import password as password_mod


def test_importable_returns_false_when_find_spec_raises() -> None:
    """``find_spec("missing.child")`` raises ModuleNotFoundError on the
    parent import — the probe must degrade to False, never propagate."""
    assert not password_mod._importable(
        "no_such_pkg_pyl42.nonexistent_child"
    )


# -- NoOpPasswordEncoder -------------------------------------------------------


def test_noop_encoder_full_contract() -> None:
    encoder = NoOpPasswordEncoder()
    assert encoder.encode("plain") == "plain"
    assert encoder.matches("plain", "plain")
    assert not encoder.matches("plain", "other")
    assert encoder.needs_upgrade("anything") is False


def test_noop_matches_non_str_returns_false() -> None:
    """hmac.compare_digest raises TypeError on a non-str/bytes operand —
    matches must swallow it as False (never-raise contract)."""
    encoder = NoOpPasswordEncoder()
    assert not encoder.matches("a", 123)  # type: ignore[arg-type]
    assert not encoder.matches(None, "a")  # type: ignore[arg-type]


# -- missing hasher libraries ----------------------------------------------------


def test_bcrypt_encoder_requires_bcrypt_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(password_mod, "_importable", lambda module: False)
    with pytest.raises(PyleafError, match="bcrypt"):
        BCryptPasswordEncoder()


def test_argon2_encoder_requires_argon2_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(password_mod, "_importable", lambda module: False)
    with pytest.raises(PyleafError, match="argon2"):
        Argon2PasswordEncoder()


def test_build_default_encoder_requires_some_hasher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(password_mod, "_importable", lambda module: False)
    with pytest.raises(PyleafError, match="No password hasher"):
        build_default_encoder()


# -- concrete encoders, malformed input ------------------------------------------


def test_bcrypt_needs_upgrade_on_malformed_hashes() -> None:
    encoder = BCryptPasswordEncoder()
    assert encoder.needs_upgrade("no-dollars-at-all")  # IndexError path
    assert encoder.needs_upgrade("$2b$xx$rest")  # ValueError path


def test_argon2_needs_upgrade_on_malformed_hash() -> None:
    """check_needs_rehash raises on a non-PHC string -> upgrade flagged."""
    assert Argon2PasswordEncoder().needs_upgrade("not-a-phc-string")


# -- DelegatingPasswordEncoder ---------------------------------------------------


def test_delegating_ctor_rejects_unregistered_default_id() -> None:
    with pytest.raises(PyleafError, match="no registered encoder"):
        DelegatingPasswordEncoder(
            "argon2", {"bcrypt": BCryptPasswordEncoder()}
        )


def test_delegating_default_id_property() -> None:
    encoder = DelegatingPasswordEncoder(
        "bcrypt", {"bcrypt": BCryptPasswordEncoder()}
    )
    assert encoder.default_id == "bcrypt"


class _ExplodingEncoder:
    """An encoder whose every method raises — the delegator must degrade."""

    def encode(self, raw_password: str) -> str:
        raise RuntimeError("boom")

    def matches(self, raw_password: str, encoded_password: str) -> bool:
        raise RuntimeError("boom")

    def needs_upgrade(self, encoded_password: str) -> bool:
        raise RuntimeError("boom")


def test_delegating_matches_swallows_encoder_exception() -> None:
    encoder = DelegatingPasswordEncoder("boom", {"boom": _ExplodingEncoder()})
    assert not encoder.matches("pw", "{boom}hash")


def test_delegating_needs_upgrade_swallows_encoder_exception() -> None:
    encoder = DelegatingPasswordEncoder("boom", {"boom": _ExplodingEncoder()})
    assert encoder.needs_upgrade("{boom}hash")
