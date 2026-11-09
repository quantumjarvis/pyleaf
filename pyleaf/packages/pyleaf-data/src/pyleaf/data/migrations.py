"""Alembic migrate-on-start for the data layer (WI 3.6, PYL-37).

The ``alembicMigrationRunner`` bean — provided here and mixed into
:class:`pyleaf.data.autoconfig.DataAutoConfiguration` — runs
``alembic upgrade head`` programmatically during ``ctx.refresh()``
whenever ``pyleaf.data.migrate-on-start`` is set. Two properties drive it:

* ``pyleaf.data.migrate-on-start`` (bool, default ``false``) — master
  switch. The ``@ConditionalOnProperty`` gate keeps the bean absent when
  the property is missing entirely; a present-but-false value still
  produces the bean but its ``@PostConstruct`` returns immediately, so
  ``migrate-on-start=false`` never touches the database.
* ``pyleaf.data.migrations-dir`` (default ``"./migrations"``) — the
  alembic script location (the directory containing ``env.py``).

Lifecycle: the runner's ``@PostConstruct`` fires inside
``ctx.refresh()``'s singleton pre-instantiation — before runners, web
serving or any app code — matching Spring Flyway/Liquibase ordering.
A migration failure propagates out of bean creation as a
``BeanCreationError`` and the refresh fails fast. ``upgrade head`` is
idempotent: a database already at head is a no-op.

Engine sharing: the runner sets ``sqlalchemy.url`` on the
``alembic.config.Config`` (password included, for plain ``env.py``
setups that build their own engine) and additionally stashes the
application ``Engine`` under ``config.attributes["connection"]`` — the
alembic cookbook convention. An ``env.py`` should treat that attribute
as the connectable::

    connectable = context.config.attributes.get("connection")
    if connectable is None:
        connectable = engine_from_config(...)
    with connectable.connect() as connection:
        context.configure(connection=connection, ...)
        with context.begin_transaction():
            context.run_migrations()

Annotation note (see the starter's autoconfigure module): ``Engine``
annotations must resolve at ``ctx.register`` time — before conditions
run — so the sqlalchemy import sits behind ``try/except ImportError``
with a placeholder class. ``alembic`` itself is only imported inside
``migrate()``: absent alembic drops the def via
``@ConditionalOnClass("alembic")`` before it can run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field
from pyleaf.boot import ConfigurationProperties
from pyleaf.core import (
    Bean,
    ConditionalOnBean,
    ConditionalOnClass,
    ConditionalOnProperty,
    PostConstruct,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
else:
    try:
        from sqlalchemy.engine import Engine
    except ImportError:
        # sqlalchemy absent — placeholder keeps the @Bean annotations
        # resolvable; the defs they annotate are dropped by
        # @ConditionalOnClass before use (starter pattern).

        class Engine:  # type: ignore[no-redef]
            """Placeholder for ``sqlalchemy.engine.Engine``."""


#: Property switching the migrate-on-start bean on/off.
MIGRATE_ON_START_PROPERTY = "pyleaf.data.migrate-on-start"

#: Property pointing at the alembic script location (``env.py``'s dir).
MIGRATIONS_DIR_PROPERTY = "pyleaf.data.migrations-dir"

#: Config.attributes key under which the application Engine is shared
#: with ``env.py`` (alembic cookbook convention).
CONNECTION_ATTRIBUTE = "connection"


@ConfigurationProperties(prefix="pyleaf.data")
class DataMigrationProperties(BaseModel):
    """Binding model for the ``pyleaf.data.*`` migration keys."""

    model_config = ConfigDict(populate_by_name=True)

    migrate_on_start: bool = Field(default=False, alias="migrate-on-start")
    migrations_dir: str = Field(default="./migrations", alias="migrations-dir")


class AlembicMigrationRunner:
    """Runs ``alembic upgrade head`` at bean-creation time.

    ``enabled`` carries the bound ``pyleaf.data.migrate-on-start`` value:
    ``@ConditionalOnProperty`` matches on presence, so an explicit
    ``false`` still creates the bean — the flag then keeps
    :meth:`migrate` a clean no-op.
    """

    def __init__(
        self, engine: Engine, migrations_dir: str, *, enabled: bool = True
    ) -> None:
        self._engine = engine
        self._migrations_dir = migrations_dir
        self._enabled = enabled

    @PostConstruct
    def migrate(self) -> None:
        """Run ``alembic upgrade head`` against the injected Engine.

        Runs inside ``ctx.refresh()`` — failure surfaces as a
        ``BeanCreationError`` and fails the refresh. Alembic is imported
        here, not at module level, so the module loads without it (the
        owning ``@Bean`` is then dropped by ``@ConditionalOnClass``).
        """
        if not self._enabled:
            return
        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", self._migrations_dir)
        cfg.set_main_option(
            "sqlalchemy.url",
            self._engine.url.render_as_string(hide_password=False),
        )
        cfg.attributes[CONNECTION_ATTRIBUTE] = self._engine
        command.upgrade(cfg, "head")


class MigrateOnStartBeans:
    """``@Bean`` provider for migrate-on-start, mixed into
    ``DataAutoConfiguration`` so the alembic wiring lives entirely in
    this module (``ctx.register`` walks the MRO for ``@Bean`` methods)."""

    @Bean
    @ConditionalOnClass("sqlalchemy")
    @ConditionalOnClass("alembic")
    @ConditionalOnProperty(MIGRATE_ON_START_PROPERTY)
    @ConditionalOnBean(Engine)
    def alembicMigrationRunner(
        self, engine: Engine, props: DataMigrationProperties
    ) -> AlembicMigrationRunner:
        """Create the migration runner wired to the (primary) Engine.

        With several ``Engine`` beans the injection resolves the
        ``@Primary`` one — multi-datasource apps migrate their primary
        datasource by default.
        """
        return AlembicMigrationRunner(
            engine, props.migrations_dir, enabled=props.migrate_on_start
        )


__all__ = [
    "CONNECTION_ATTRIBUTE",
    "MIGRATE_ON_START_PROPERTY",
    "MIGRATIONS_DIR_PROPERTY",
    "AlembicMigrationRunner",
    "DataMigrationProperties",
    "MigrateOnStartBeans",
]
