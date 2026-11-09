"""PasswordEncoder family: argon2, bcrypt, delegating ``{id}`` format (WI 3.8).

Pinned semantics: ``matches`` never raises; ``{noop}`` is rejected unless
``allow_noop=True``; noop is NEVER the default; ``needs_upgrade`` flags
non-default ids, parameter drift and un-prefixed hashes;
``upgrade_if_needed`` implements upgrade-on-login.
"""

from __future__ import annotations

from helpers import build_ctx
from pyleaf.core import Bean, Configuration
from pyleaf.security import (
    Argon2PasswordEncoder,
    BCryptPasswordEncoder,
    DelegatingPasswordEncoder,
    NoOpPasswordEncoder,
    PasswordEncoder,
    build_default_encoder,
)
from pyleaf.security.autoconfig import SecurityAutoConfiguration


def test_argon2_round_trip() -> None:
    encoder = Argon2PasswordEncoder()
    hashed = encoder.encode("s3cret!")
    assert hashed.startswith("$argon2")
    assert encoder.matches("s3cret!", hashed)
    assert not encoder.matches("wrong", hashed)
    assert not encoder.needs_upgrade(hashed)


def test_bcrypt_round_trip() -> None:
    encoder = BCryptPasswordEncoder()
    hashed = encoder.encode("s3cret!")
    assert hashed.startswith("$2")
    assert encoder.matches("s3cret!", hashed)
    assert not encoder.matches("wrong", hashed)
    assert not encoder.needs_upgrade(hashed)


def test_bcrypt_needs_upgrade_on_cost_drift() -> None:
    weak = BCryptPasswordEncoder(rounds=4)
    hashed = weak.encode("pw")
    assert BCryptPasswordEncoder(rounds=10).needs_upgrade(hashed)
    assert not weak.needs_upgrade(hashed)


def test_delegating_encode_prefixes_default_id() -> None:
    encoder = build_default_encoder()
    hashed = encoder.encode("pw")
    assert hashed.startswith("{argon2}")
    assert encoder.matches("pw", hashed)
    assert not encoder.needs_upgrade(hashed)


def test_delegating_reads_bcrypt_prefix() -> None:
    """A {bcrypt} hash verifies even when argon2 is the default id."""
    raw = BCryptPasswordEncoder().encode("pw")
    encoder = build_default_encoder()
    assert encoder.matches("pw", "{bcrypt}" + raw)


def test_needs_upgrade_on_non_default_id() -> None:
    """Legacy-format pin: stored {bcrypt} under an argon2 default upgrades."""
    raw = BCryptPasswordEncoder().encode("pw")
    encoder = build_default_encoder()
    stored = "{bcrypt}" + raw
    assert encoder.needs_upgrade(stored)
    upgraded = encoder.upgrade_if_needed("pw", stored)
    assert upgraded is not None
    assert upgraded.startswith("{argon2}")
    # The upgraded hash verifies and is no longer stale.
    assert encoder.matches("pw", upgraded)
    assert not encoder.needs_upgrade(upgraded)


def test_no_upgrade_when_hash_current_or_password_wrong() -> None:
    encoder = build_default_encoder()
    current = encoder.encode("pw")
    assert encoder.upgrade_if_needed("pw", current) is None
    legacy = "{bcrypt}" + BCryptPasswordEncoder().encode("pw")
    assert encoder.upgrade_if_needed("WRONG", legacy) is None


def test_noop_rejected_unless_explicitly_allowed() -> None:
    """{noop} hashes never verify on the auto-configured encoder."""
    encoder = build_default_encoder()
    assert not encoder.matches("plain", "{noop}plain")
    assert encoder.needs_upgrade("{noop}plain")
    # Opt-in allows decoding — still never the default id.
    allowed = DelegatingPasswordEncoder(
        "argon2",
        {"argon2": Argon2PasswordEncoder()},
        allow_noop=True,
    )
    assert allowed.matches("plain", "{noop}plain")


def test_unmapped_id_and_malformed_hash_match_false() -> None:
    encoder = build_default_encoder()
    assert not encoder.matches("pw", "{sha256}deadbeef")
    assert not encoder.matches("pw", "not-a-hash")
    assert not encoder.matches("pw", "{unclosed")
    assert encoder.needs_upgrade("not-a-hash")


def test_concrete_encoders_reject_malformed_hashes() -> None:
    assert not Argon2PasswordEncoder().matches("pw", "garbage")
    assert not BCryptPasswordEncoder().matches("pw", "garbage")
    assert not NoOpPasswordEncoder().matches("a", "b")


def test_autoconfig_provides_delegating_encoder() -> None:
    ctx = build_ctx(SecurityAutoConfiguration)
    bean = ctx.get_bean("passwordEncoder")
    assert isinstance(bean, DelegatingPasswordEncoder)
    assert ctx.get(PasswordEncoder) is bean


class MyEncoder:
    """A user-side PasswordEncoder (module level so hints resolve)."""

    def encode(self, raw_password: str) -> str:
        return "custom"

    def matches(self, raw_password: str, encoded_password: str) -> bool:
        return encoded_password.endswith("custom")

    def needs_upgrade(self, encoded_password: str) -> bool:
        return False


@Configuration
class UserEncoderConfig:
    @Bean
    def myEncoder(self) -> MyEncoder:
        return MyEncoder()


def test_user_password_encoder_backs_off() -> None:
    """A user PasswordEncoder bean wins over the auto-configured one."""
    ctx = build_ctx(UserEncoderConfig, SecurityAutoConfiguration)
    assert isinstance(ctx.get(PasswordEncoder), MyEncoder)
