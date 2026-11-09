"""Alembic migrate-on-start: postgres testcontainer + a real alembic env
in ``tmp_path`` (WI 3.6, PYL-37). Docker-dependent tests skip cleanly.
"""

import textwrap
from pathlib import Path

import pytest
from pyleaf.core import (
    ApplicationContext,
    BeanCreationError,
    Environment,
    NoSuchBean,
    PropertySource,
)
from pyleaf.core.conditions import (
    ConditionContext,
    OnClassCondition,
    conditions_of,
    evaluate_condition,
)
from pyleaf.core.definition import BeanRegistry
from pyleaf.data.autoconfig import DataAutoConfiguration
from pyleaf.data.migrations import AlembicMigrationRunner, MigrateOnStartBeans
from pyleaf.starter.sqlalchemy.autoconfigure import SqlAlchemyAutoConfiguration
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.engine import Engine

_ENV_PY = textwrap.dedent(
    '''
    from alembic import context


    def run_migrations_online() -> None:
        # The migration bean shares the application Engine through
        # ``config.attributes["connection"]``; fall back to a fresh
        # engine built from ``sqlalchemy.url`` for a plain env.
        connectable = context.config.attributes.get("connection")
        if connectable is None:
            from sqlalchemy import engine_from_config, pool

            section = (
                context.config.get_section(
                    context.config.config_ini_section
                )
                or {}
            )
            connectable = engine_from_config(
                section, prefix="sqlalchemy.", poolclass=pool.NullPool
            )
        with connectable.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()


    run_migrations_online()
    '''
)

_REVISION_PY = textwrap.dedent(
    '''
    """create the alembic_items table."""

    import sqlalchemy as sa
    from alembic import op

    revision = "0001"
    down_revision = None
    branch_labels = None
    depends_on = None


    def upgrade() -> None:
        op.create_table(
            "alembic_items",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(64)),
        )


    def downgrade() -> None:
        op.drop_table("alembic_items")
    '''
)


def _write_alembic_env(tmp_path: Path) -> Path:
    """A real alembic script tree: env.py + one revision creating a table."""
    migrations = tmp_path / "migrations"
    (migrations / "versions").mkdir(parents=True)
    (migrations / "env.py").write_text(_ENV_PY)
    (migrations / "versions" / "0001_create_items.py").write_text(_REVISION_PY)
    return migrations


def _ctx(values: dict[str, object]) -> ApplicationContext:
    ctx = ApplicationContext(
        environment=Environment(sources=[PropertySource("test", values)])
    )
    ctx.register(SqlAlchemyAutoConfiguration)
    ctx.register(DataAutoConfiguration)
    ctx.refresh()
    return ctx


def _values(url: str, migrations: Path | None) -> dict[str, object]:
    values: dict[str, object] = {
        "app.datasource.url": url,
        "pyleaf.data.migrate-on-start": True,
    }
    if migrations is not None:
        values["pyleaf.data.migrations-dir"] = str(migrations)
    return values


def test_upgrade_head_applied_on_refresh(
    postgres_dsn: str, tmp_path: Path
) -> None:
    """``alembic upgrade head`` runs during ``ctx.refresh()`` — the table
    from the revision exists before the context serves beans."""
    pytest.importorskip("alembic")
    migrations = _write_alembic_env(tmp_path)
    ctx = _ctx(_values(f"postgresql+psycopg://{postgres_dsn}", migrations))
    try:
        engine = ctx.get(Engine)
        assert sa_inspect(engine).has_table("alembic_items")
        assert sa_inspect(engine).has_table("alembic_version")
    finally:
        ctx.close()


def test_second_context_is_a_noop(
    postgres_dsn: str, tmp_path: Path
) -> None:
    """Idempotent: ``alembic_version`` already at head — a second context
    refresh runs upgrade again and changes nothing."""
    pytest.importorskip("alembic")
    migrations = _write_alembic_env(tmp_path)
    values = _values(f"postgresql+psycopg://{postgres_dsn}", migrations)

    ctx = _ctx(values)
    ctx.close()

    ctx2 = _ctx(values)  # must not raise: upgrade head on a head DB
    try:
        engine = ctx2.get(Engine)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT version_num FROM alembic_version")).all()
        assert rows == [("0001",)]
    finally:
        ctx2.close()


def test_migrations_skipped_when_property_absent(tmp_path: Path) -> None:
    """No ``pyleaf.data.migrate-on-start`` -> def dropped in phase A with
    a ``@ConditionalOnProperty`` reason; no bean, no binding."""
    ctx = _ctx({"app.datasource.url": f"sqlite:///{tmp_path}/app.db"})
    try:
        with pytest.raises(NoSuchBean):
            ctx.get_bean("alembicMigrationRunner")

        entries = {e.bean_name: e for e in ctx.condition_report.entries}
        entry = entries["alembicMigrationRunner"]
        assert entry.matched is False
        assert any("@ConditionalOnProperty" in r for r in entry.reasons)
    finally:
        ctx.close()


def test_migrations_noop_when_property_false(tmp_path: Path) -> None:
    """``migrate-on-start=false`` is present so the bean exists, but the
    bound ``migrate_on_start`` flag keeps ``migrate()`` a no-op — no
    ``alembic_version`` table is ever created."""
    pytest.importorskip("alembic")
    ctx = _ctx(
        {
            "app.datasource.url": f"sqlite:///{tmp_path}/app.db",
            "pyleaf.data.migrate-on-start": False,
        }
    )
    try:
        runner = ctx.get_bean("alembicMigrationRunner")
        assert isinstance(runner, AlembicMigrationRunner)
        engine = ctx.get(Engine)
        assert not sa_inspect(engine).has_table("alembic_version")
    finally:
        ctx.close()


def test_migration_failure_fails_refresh(tmp_path: Path) -> None:
    """migrate-on-start=true with a bogus migrations dir -> the bean's
    creation raises -> ``ctx.refresh()`` fails fast."""
    pytest.importorskip("alembic")
    values = _values(
        f"sqlite:///{tmp_path}/app.db", tmp_path / "does-not-exist"
    )
    with pytest.raises(BeanCreationError):
        _ctx(values)


def test_migration_bean_gated_on_alembic_class() -> None:
    """The OnClass("alembic") gate: when alembic is not importable the
    condition does not match and the def would be dropped — asserted at
    the condition level (alembic is installed in the dev env)."""
    conditions = conditions_of(MigrateOnStartBeans.alembicMigrationRunner)
    on_class = [c for c in conditions if isinstance(c, OnClassCondition)]
    alembic = [c for c in on_class if c.name == "alembic"]
    assert alembic, "expected an @ConditionalOnClass('alembic') gate"

    fake_ctx = ConditionContext(
        Environment(),
        registry=BeanRegistry(),
        is_importable=lambda _name: False,
        excluding="alembicMigrationRunner",
    )
    result = evaluate_condition(alembic[0], fake_ctx)
    assert result.matched is False
    assert "alembic" in result.reason
