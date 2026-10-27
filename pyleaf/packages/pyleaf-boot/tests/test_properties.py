"""``@ConfigurationProperties`` binds environment keys to pydantic models."""

from typing import Any

from pydantic import BaseModel, Field
from pyleaf.boot import ConfigurationProperties
from pyleaf.core import ApplicationContext, Environment, PropertySource


class PoolProps(BaseModel):
    """Nested properties model."""

    size: int = 10
    timeout: float = 1.5


@ConfigurationProperties(prefix="app.db")
class DbProps(BaseModel):
    """Props bean under test — deliberately NO @Component stereotype."""

    url: str
    port: int = 5432
    pool: PoolProps = Field(default_factory=PoolProps)


def _ctx(values: dict[str, Any]) -> ApplicationContext:
    env = Environment(sources=[PropertySource("test", values)])
    return ApplicationContext(environment=env)


def test_binds_values_from_environment_keys() -> None:
    ctx = _ctx(
        {
            "app.db.url": "postgres://db",
            "app.db.port": "6543",
            "app.db.pool.size": "20",
            "unrelated.key": "ignored",
        }
    )
    ctx.register(DbProps)

    props = ctx.get(DbProps)

    assert props.url == "postgres://db"
    assert props.port == 6543  # coerced from the string source value
    assert isinstance(props.pool, PoolProps)
    assert props.pool.size == 20
    assert props.pool.timeout == 1.5  # nested model default survives


def test_model_defaults_fill_unset_fields() -> None:
    ctx = _ctx({"app.db.url": "postgres://db"})
    ctx.register(DbProps)

    props = ctx.get(DbProps)

    assert props.port == 5432
    assert props.pool.size == 10


def test_registers_without_component_stereotype() -> None:
    """A props class carries no component marker yet registers cleanly."""
    assert getattr(DbProps, "__pyleaf_component__", None) is None
    assert getattr(DbProps, "__pyleaf_configuration__", None) is None

    ctx = _ctx({"app.db.url": "postgres://db"})
    ctx.register(DbProps)

    assert ctx.get(DbProps).url == "postgres://db"


def test_bean_name_is_decapitalized_class_name() -> None:
    ctx = _ctx({"app.db.url": "postgres://db"})
    ctx.register(DbProps)

    assert ctx.get_bean("dbProps").url == "postgres://db"


def test_named_props_bean() -> None:
    @ConfigurationProperties(prefix="app.db", name="mainDb")
    class NamedDbProps(BaseModel):
        url: str = "unset"

    ctx = _ctx({"app.db.url": "postgres://db"})
    ctx.register(NamedDbProps)

    assert ctx.get_bean("mainDb").url == "postgres://db"
