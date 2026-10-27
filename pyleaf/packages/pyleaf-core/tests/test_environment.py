"""Tests for Environment: property sources, coercion, env vars, profiles."""

import pytest
from pyleaf.core import (
    ApplicationContext,
    Environment,
    EnvVarSource,
    PropertySource,
    PyleafError,
)


def test_later_property_source_overrides_earlier() -> None:
    s1 = PropertySource("a", {"app.name": "x", "app.port": "8080"})
    s2 = PropertySource("b", {"app.name": "y"})
    env = Environment(sources=[s1, s2])
    assert env.get("app.name") == "y"
    assert env.get("app.port") == "8080"


def test_get_coerces_int() -> None:
    env = Environment(sources=[PropertySource("a", {"app.port": "8080"})])
    port = env.get("app.port", int)
    assert port == 8080
    assert isinstance(port, int)


def test_get_coerces_bool() -> None:
    env = Environment(
        sources=[PropertySource("a", {"flag.on": "true", "flag.off": "false"})]
    )
    assert env.get("flag.on", bool) is True
    assert env.get("flag.off", bool) is False


def test_get_missing_key_returns_default() -> None:
    env = Environment(sources=[PropertySource("a", {"app.name": "x"})])
    assert env.get("missing") is None
    assert env.get("missing", default="fallback") == "fallback"
    assert env.get("missing", int, default=7) == 7


def test_require_returns_value_and_raises_for_missing() -> None:
    env = Environment(sources=[PropertySource("a", {"app.name": "x"})])
    assert env.require("app.name") == "x"
    with pytest.raises(PyleafError):
        env.require("missing")


def test_env_var_source_maps_prefixed_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYLEAF_APP_NAME", "hello")
    env = Environment(sources=[EnvVarSource(prefix="PYLEAF_")])
    assert env.get("app.name") == "hello"


def test_env_var_source_ignores_other_prefixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYLEAF_APP_NAME", raising=False)
    monkeypatch.setenv("OTHER_APP_NAME", "nope")
    env = Environment(sources=[EnvVarSource(prefix="PYLEAF_")])
    assert env.get("app.name") is None


def test_context_environment_kwarg_exposed_as_bean() -> None:
    env = Environment(sources=[PropertySource("a", {"app.name": "x"})])
    ctx = ApplicationContext(environment=env)
    assert ctx.get(Environment) is env


def test_context_default_environment_is_a_bean() -> None:
    ctx = ApplicationContext()
    assert isinstance(ctx.get(Environment), Environment)


def test_active_profiles_contains_configured() -> None:
    env = Environment(active_profiles=["dev", "local"])
    assert "dev" in env.active_profiles
    assert "local" in env.active_profiles


def test_empty_profiles_means_default_active() -> None:
    assert "default" in Environment().active_profiles
