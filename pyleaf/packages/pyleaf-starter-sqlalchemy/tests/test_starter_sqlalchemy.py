"""Contract tests for the pyleaf-starter-sqlalchemy auto-configuration."""

import importlib.util
import subprocess
import sys
from typing import Any

import pytest
import sqlalchemy
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Configuration,
    Environment,
    NoSuchBean,
    PropertySource,
)
from pyleaf.starter.sqlalchemy.autoconfigure import (
    DataSourceProperties,
    SqlAlchemyAutoConfiguration,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_DEFAULT_VALUES: dict[str, object] = {
    "app.datasource.url": "sqlite:///:memory:",
}


def _ctx(
    *classes: type, values: dict[str, object] | None = None
) -> ApplicationContext:
    ctx = ApplicationContext(
        environment=Environment(
            sources=[PropertySource("test", values or _DEFAULT_VALUES)]
        )
    )
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def _report(ctx: ApplicationContext) -> dict[str, Any]:
    return {entry.bean_name: entry for entry in ctx.condition_report.entries}


def test_module_is_loadable_without_sqlalchemy() -> None:
    """The autoconfigure module must import when sqlalchemy is absent, so the
    entry point loads and @ConditionalOnClass — not an ImportError — decides."""
    code = (
        "import sys\n"
        "sys.modules['sqlalchemy'] = None\n"
        "import pyleaf.starter.sqlalchemy.autoconfigure as m\n"
        "assert m.SqlAlchemyAutoConfiguration is not None\n"
        "assert m.DataSourceProperties is not None\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter + literal code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_engine_bean_created_from_bound_properties() -> None:
    ctx = _ctx(SqlAlchemyAutoConfiguration)

    engine = ctx.get(Engine)

    assert isinstance(engine, Engine)
    assert ctx.get_bean("engine") is engine


def test_create_engine_honors_datasource_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """url/pool_size/echo from app.datasource.* reach create_engine."""
    captured: dict[str, Any] = {}
    real_create_engine = sqlalchemy.create_engine

    def spy(url: Any, **kwargs: Any) -> Engine:
        captured["url"] = url
        captured.update(kwargs)
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(sqlalchemy, "create_engine", spy)

    ctx = _ctx(
        SqlAlchemyAutoConfiguration,
        values={
            "app.datasource.url": "sqlite:///:memory:",
            "app.datasource.pool_size": 7,
            "app.datasource.echo": True,
        },
    )
    ctx.get(Engine)

    assert captured["url"] == "sqlite:///:memory:"
    assert captured["pool_size"] == 7
    assert captured["echo"] is True


def test_datasource_properties_bean_binds_prefix() -> None:
    """The @ConfigurationProperties model is bound under app.datasource.*."""
    ctx = _ctx(SqlAlchemyAutoConfiguration)
    ctx.get(Engine)  # engine creation pulls in the properties bean

    props = ctx.get(DataSourceProperties)
    assert props.url == "sqlite:///:memory:"
    assert props.pool_size == 5
    assert props.echo is False


def test_sessionfactory_bean_is_sessionmaker_bound_to_engine() -> None:
    ctx = _ctx(SqlAlchemyAutoConfiguration)

    factory = ctx.get(sessionmaker)

    assert isinstance(factory, sessionmaker)
    assert factory.kw.get("bind") is ctx.get_bean("engine")
    session = factory()
    assert isinstance(session, Session)
    session.close()


def test_user_engine_bean_backs_off_with_onmissingbean_reason() -> None:
    """A user-provided Engine bean drops the starter's engine def."""

    @Configuration
    class UserDatabase:
        @Bean
        def myEngine(self) -> Engine:
            return create_engine("sqlite:///:memory:")

    ctx = _ctx(UserDatabase, SqlAlchemyAutoConfiguration)

    engine = ctx.get(Engine)
    assert engine is ctx.get_bean("myEngine")
    with pytest.raises(NoSuchBean):
        ctx.get_bean("engine")

    entry = _report(ctx)["engine"]
    assert entry.matched is False
    assert any(
        "@ConditionalOnMissingBean" in reason for reason in entry.reasons
    )
    # The session factory still wires up, bound to the user's engine.
    assert ctx.get(sessionmaker).kw.get("bind") is engine


def test_missing_sqlalchemy_reports_onclass_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When sqlalchemy is not importable, every starter def is dropped with
    an @ConditionalOnClass reason — never an import error."""
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    ctx = _ctx(SqlAlchemyAutoConfiguration)

    with pytest.raises(NoSuchBean):
        ctx.get_bean("engine")
    with pytest.raises(NoSuchBean):
        ctx.get_bean("sessionfactory")
    with pytest.raises(NoSuchBean):
        ctx.get_bean("sqlAlchemyAutoConfiguration")

    entries = _report(ctx)
    for bean_name in (
        "sqlAlchemyAutoConfiguration",
        "engine",
        "sessionfactory",
    ):
        entry = entries[bean_name]
        assert entry.matched is False
        assert any(
            "is not importable" in reason for reason in entry.reasons
        ), f"{bean_name}: {entry.reasons}"
