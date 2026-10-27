"""Failure modes: bad property values and malformed yaml."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pyleaf.boot import ConfigurationProperties, load_environment
from pyleaf.core import ApplicationContext, Environment, PropertySource, PyleafError


@ConfigurationProperties(prefix="app.db")
class DbProps(BaseModel):
    url: str = "unset"
    port: int = 5432


def _ctx(values: dict[str, Any]) -> ApplicationContext:
    env = Environment(sources=[PropertySource("test", values)])
    return ApplicationContext(environment=env)


def test_invalid_value_names_key_path_and_bad_value() -> None:
    """``app.db.port=abc`` must fail naming 'port' and 'abc'."""
    ctx = _ctx({"app.db.url": "x", "app.db.port": "abc"})
    ctx.register(DbProps)

    with pytest.raises(PyleafError) as excinfo:
        ctx.get(DbProps)

    msg = str(excinfo.value)
    assert "port" in msg
    assert "abc" in msg
    assert "dbProps" in msg  # the bean name is named too


def test_invalid_value_fails_at_refresh() -> None:
    """Props beans bind eagerly: refresh() surfaces the same error."""
    ctx = _ctx({"app.db.url": "x", "app.db.port": "abc"})
    ctx.register(DbProps)

    with pytest.raises(PyleafError) as excinfo:
        ctx.refresh()

    assert "abc" in str(excinfo.value)


def test_malformed_yaml_names_the_file(tmp_path: Path) -> None:
    bad = tmp_path / "application.yaml"
    bad.write_text("a: [unclosed\n", encoding="utf-8")

    with pytest.raises(PyleafError) as excinfo:
        load_environment(config_dir=tmp_path, env={})

    assert "application.yaml" in str(excinfo.value)


def test_missing_profile_yaml_is_not_an_error(tmp_path: Path) -> None:
    """``application-dev.yaml`` absent: load succeeds with defaults."""
    env = load_environment(config_dir=tmp_path, profiles=["dev"], env={})

    assert env.get("anything.at.all") is None
