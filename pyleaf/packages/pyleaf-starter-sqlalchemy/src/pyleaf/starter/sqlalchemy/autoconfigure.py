"""SQLAlchemy starter auto-configuration.

Registered through the ``pyleaf.autoconfigure`` entry point. Provides an
:class:`~sqlalchemy.engine.Engine` bean and a
:class:`~sqlalchemy.orm.sessionmaker` bean when sqlalchemy is installed and
no user ``Engine`` bean exists.

Design note — referencing sqlalchemy while staying loadable without it:
``@Bean`` return annotations are resolved eagerly at ``ctx.register`` time
(``bean_return_type`` -> ``get_type_hints``), *before* conditions run in
``refresh()``. A plain top-level ``import sqlalchemy`` would therefore make
``ep.load()`` itself fail when the package is absent, surfacing as "entry
point load failed" instead of the required ``@ConditionalOnClass`` reason;
a ``TYPE_CHECKING``-only import would leave the names unresolvable at
register() time. So the sqlalchemy *types* are imported inside a
``try/except ImportError`` that falls back to placeholder classes: the
module always loads, annotations always resolve to real (``issubclass``-
compatible) classes, and ``@ConditionalOnClass`` then drops every def of
this module in phase A when sqlalchemy is absent. Callables such as
``create_engine`` are imported inside the method bodies — they only run
when the bean is actually created, which never happens for a dropped def.

``@ConditionalOnClass`` is also repeated on each ``@Bean`` method: core
does not propagate class-level conditions to bean-method definitions, so
without their own gate the method defs would survive phase A (and their
eager factories would then fail on the missing configuration instance).

The starter also backs off when no datasource is configured: ``engine``
carries ``@ConditionalOnProperty("app.datasource.url")`` so an application
that installs the starter without configuring a url stays unaffected
(no binding attempt, no engine). ``sessionfactory`` carries
``@ConditionalOnBean(Engine)`` so it wires to whichever ``Engine`` bean
survives — the starter's or a user-provided one — and backs off when no
engine exists at all.

The ``DataSourceProperties`` model is *not* registered eagerly: a
``@ConfigurationProperties`` class used as a ``@Bean``-method parameter is
registered on demand by ``ApplicationContext._resolve_single``'s
stereotype fallback, so the binding only happens when the engine bean is
really created.
"""

from __future__ import annotations

import inspect
from typing import Any, ClassVar

from pydantic import BaseModel
from pyleaf.boot import AutoConfiguration, ConfigurationProperties
from pyleaf.core import (
    Bean,
    Conditional,
    ConditionalOnBean,
    ConditionalOnClass,
    ConditionalOnMissingBean,
    ConditionalOnProperty,
    ConditionContext,
    ConditionResult,
)
from pyleaf.tx import AsyncTransactionManager, TransactionManager

try:
    from pyleaf.starter.sqlalchemy.tx import (
        AsyncSqlAlchemyTransactionManager,
        SqlAlchemyTransactionManager,
    )

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import sessionmaker
except ImportError:
    # sqlalchemy absent — placeholder classes keep this module importable
    # and the @Bean annotations resolvable (see module docstring). The defs
    # they annotate are dropped by @ConditionalOnClass before use.

    class Engine:  # type: ignore[no-redef]
        """Placeholder for ``sqlalchemy.engine.Engine``."""

    class sessionmaker:  # type: ignore[no-redef]
        """Placeholder for ``sqlalchemy.orm.sessionmaker``."""

    class SqlAlchemyTransactionManager:  # type: ignore[no-redef]
        """Placeholder for ``tx.SqlAlchemyTransactionManager``."""

    class AsyncSqlAlchemyTransactionManager:  # type: ignore[no-redef]
        """Placeholder for ``tx.AsyncSqlAlchemyTransactionManager``."""

try:
    from sqlalchemy.ext.asyncio import async_sessionmaker
except ImportError:
    # sqlalchemy absent OR greenlet missing (ext.asyncio raises ImportError
    # at import); defs annotated with it are dropped by @ConditionalOnClass.

    class async_sessionmaker:  # type: ignore[no-redef]
        """Placeholder for ``sqlalchemy.ext.asyncio.async_sessionmaker``."""


def _has_coroutine_begin(bean_type: Any) -> bool:
    """True when the bean type's ``begin`` is a coroutine function."""
    return inspect.iscoroutinefunction(getattr(bean_type, "begin", None))


class OnMissingSyncTransactionManagerCondition:
    """Match when no *synchronous* ``TransactionManager`` bean exists.

    ``@ConditionalOnMissingBean(TransactionManager)`` cannot be used
    directly: a ``runtime_checkable`` protocol's ``issubclass`` only
    checks method presence, so a coroutine-``begin`` manager structurally
    satisfies the sync protocol. This condition additionally requires the
    candidate's ``begin`` to be a plain (non-coroutine) callable.
    """

    bean_presence: ClassVar[bool] = True

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        found = [
            defn.name
            for defn in ctx.registry.candidates(TransactionManager)
            if defn.name != ctx.excluding
            and not _has_coroutine_begin(defn.bean_type)
        ]
        names = ", ".join(found) if found else "none"
        return ConditionResult(
            not found,
            f"OnMissingSyncTransactionManager found beans: {names}",
        )


class OnMissingAsyncTransactionManagerCondition:
    """Match when no coroutine-``begin`` transaction manager bean exists
    (mirror of :class:`OnMissingSyncTransactionManagerCondition`)."""

    bean_presence: ClassVar[bool] = True

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        found = [
            defn.name
            for defn in ctx.registry.candidates(AsyncTransactionManager)
            if defn.name != ctx.excluding
            and _has_coroutine_begin(defn.bean_type)
        ]
        names = ", ".join(found) if found else "none"
        return ConditionResult(
            not found,
            f"OnMissingAsyncTransactionManager found beans: {names}",
        )


@ConfigurationProperties(prefix="app.datasource")
class DataSourceProperties(BaseModel):
    """Binding model for the ``app.datasource.*`` configuration keys."""

    url: str
    pool_size: int = 5
    echo: bool = False


@AutoConfiguration
@ConditionalOnClass("sqlalchemy")
class SqlAlchemyAutoConfiguration:
    """Back off to a user ``Engine``; otherwise build one from properties."""

    @Bean
    @ConditionalOnClass("sqlalchemy")
    @ConditionalOnMissingBean(Engine)
    @ConditionalOnProperty("app.datasource.url")
    def engine(self, props: DataSourceProperties) -> Engine:
        """Create the engine from the bound ``app.datasource.*`` values."""
        from sqlalchemy import create_engine

        return create_engine(
            props.url,
            pool_size=props.pool_size,
            echo=props.echo,
        )

    @Bean
    @ConditionalOnClass("sqlalchemy")
    @ConditionalOnBean(Engine)
    def sessionfactory(self, engine: Engine) -> sessionmaker:  # type: ignore[type-arg]
        """Create a ``sessionmaker`` bound to the (possibly user) engine."""
        return sessionmaker(bind=engine)

    @Bean
    @ConditionalOnClass("sqlalchemy")
    @ConditionalOnBean(sessionmaker)
    @Conditional(OnMissingSyncTransactionManagerCondition)
    def sqlalchemyTransactionManager(
        self, sessionfactory: sessionmaker  # type: ignore[type-arg]
    ) -> SqlAlchemyTransactionManager:
        """Create the sync transaction manager over the session factory.

        Wires to whichever ``sessionmaker`` bean survives (the starter's
        or a user's) and backs off when the user provides their own
        ``TransactionManager``.
        """
        return SqlAlchemyTransactionManager(sessionfactory)

    @Bean
    @ConditionalOnClass("sqlalchemy")
    @ConditionalOnClass("greenlet")
    @ConditionalOnBean(async_sessionmaker)
    @Conditional(OnMissingAsyncTransactionManagerCondition)
    def sqlalchemyAsyncTransactionManager(
        self, sessionfactory: async_sessionmaker  # type: ignore[type-arg]
    ) -> AsyncSqlAlchemyTransactionManager:
        """Create the async transaction manager when an
        ``async_sessionmaker`` bean exists (the starter does not create
        one itself — async engines are user-configured). Backs off to a
        user ``AsyncTransactionManager``. ``greenlet`` is gated on class
        because ``sqlalchemy.ext.asyncio`` raises ImportError without it.
        """
        return AsyncSqlAlchemyTransactionManager(sessionfactory)


__all__ = [
    "DataSourceProperties",
    "SqlAlchemyAutoConfiguration",
]
