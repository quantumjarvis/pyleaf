"""Multi-datasource via qualifiers + per-datasource tx isolation (WI 3.6).

The starter's ``app.datasource.url`` engine is the primary datasource for
single-datasource apps. For a second datasource the user takes over
datasource config entirely (any user ``Engine`` bean already backs the
starter's ``@ConditionalOnMissingBean`` engine off): a ``@Primary``
``@Bean`` for the primary side and named ``@Bean``s — resolved through
``Annotated[..., Qualifier("name")]`` — for the secondary side.
"""

from pathlib import Path
from typing import Annotated

import pytest
from data_models import Base, Item
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Configuration,
    NoUniqueBean,
    Primary,
    Qualifier,
    Service,
)
from pyleaf.data import CrudRepository
from pyleaf.starter.sqlalchemy.tx import SqlAlchemyTransactionManager
from pyleaf.tx import Transactional
from pyleaf.tx.autoconfigure import TxAutoConfiguration
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


class PrimaryItemRepo(CrudRepository[Item, int], bean=True):
    """Repo bound to the primary datasource: the inherited constructor
    injects ``SqlAlchemyTransactionManager`` unqualified, which resolves
    the ``@Primary`` manager bean."""


class ReportingItemRepo(CrudRepository[Item, int], bean=True):
    """Repo bound to the secondary datasource via a qualified TM."""

    def __init__(
        self,
        tx_manager: Annotated[
            SqlAlchemyTransactionManager, Qualifier("reportingTxManager")
        ],
    ) -> None:
        super().__init__(tx_manager)


@Service
class PrimaryWriter:
    """Writes on the primary datasource: bare ``@Transactional`` resolves
    the primary ``TransactionManager`` by type."""

    def __init__(self, repo: PrimaryItemRepo) -> None:
        self._repo = repo

    @Transactional
    def add(self, name: str) -> None:
        self._repo.save(Item(name=name, price=1))

    @Transactional
    def add_then_fail(self, name: str) -> None:
        self._repo.save(Item(name=name, price=1))
        raise RuntimeError("boom")


@Service
class ReportingWriter:
    """Writes on the secondary datasource via ``manager=`` by name."""

    def __init__(self, repo: ReportingItemRepo) -> None:
        self._repo = repo

    @Transactional(manager="reportingTxManager")
    def add(self, name: str) -> None:
        self._repo.save(Item(name=name, price=1))


def _two_datasource_ctx(tmp_path: Path) -> tuple[ApplicationContext, Engine, Engine]:
    """A refreshed context: two user datasources — a ``@Primary`` pair and
    a ``reporting``-qualified pair of engine/sessionmaker/TM."""
    engine_a = create_engine(f"sqlite:///{tmp_path}/primary.db")
    Base.metadata.create_all(engine_a)
    engine_b = create_engine(f"sqlite:///{tmp_path}/reporting.db")
    Base.metadata.create_all(engine_b)

    @Configuration
    class TwoSources:
        @Bean
        @Primary
        def engine(self) -> Engine:
            return engine_a

        @Bean(name="reporting")
        def reporting_engine(self) -> Engine:
            return engine_b

        @Bean
        @Primary
        def sessionfactory(self, engine: Engine) -> sessionmaker:  # type: ignore[type-arg]
            return sessionmaker(bind=engine)

        @Bean(name="reportingSessionFactory")
        def reporting_session_factory(
            self,
            reporting: Annotated[Engine, Qualifier("reporting")],
        ) -> sessionmaker:  # type: ignore[type-arg]
            return sessionmaker(bind=reporting)

        @Bean
        @Primary
        def txManager(
            self, sessionfactory: sessionmaker  # type: ignore[type-arg]
        ) -> SqlAlchemyTransactionManager:
            return SqlAlchemyTransactionManager(sessionfactory)

        @Bean(name="reportingTxManager")
        def reporting_tx_manager(
            self,
            sessionfactory: Annotated[
                sessionmaker, Qualifier("reportingSessionFactory")  # type: ignore[type-arg]
            ],
        ) -> SqlAlchemyTransactionManager:
            return SqlAlchemyTransactionManager(sessionfactory)

    ctx = ApplicationContext()
    ctx.register(TxAutoConfiguration)
    ctx.register(TwoSources)
    ctx.register(PrimaryItemRepo)
    ctx.register(ReportingItemRepo)
    ctx.register(PrimaryWriter)
    ctx.register(ReportingWriter)
    ctx.refresh()
    return ctx, engine_a, engine_b


def _names(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.scalars(select(Item.name)).all())


def _count(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.scalar(select(func.count()).select_from(Item)) or 0)


def test_primary_wins_unqualified_and_qualifier_selects_secondary(
    tmp_path: Path,
) -> None:
    ctx, engine_a, engine_b = _two_datasource_ctx(tmp_path)
    try:
        assert ctx.get(Engine) is engine_a
        qualified = ctx.get(Annotated[Engine, Qualifier("reporting")])
        assert qualified is engine_b
    finally:
        ctx.close()


def test_two_unqualified_engines_raise_no_unique(tmp_path: Path) -> None:
    """Two Engine beans with no @Primary -> NoUniqueBean on bare get."""

    @Configuration
    class TwoPlain:
        @Bean
        def engine(self) -> Engine:
            return create_engine(f"sqlite:///{tmp_path}/a.db")

        @Bean(name="engine2")
        def second_engine(self) -> Engine:
            return create_engine(f"sqlite:///{tmp_path}/b.db")

    ctx = ApplicationContext()
    ctx.register(TwoPlain)
    ctx.refresh()
    try:
        with pytest.raises(NoUniqueBean):
            ctx.get(Engine)
    finally:
        ctx.close()


def test_per_datasource_tx_isolation(tmp_path: Path) -> None:
    """A write under the primary TM lands on the primary DB only; a write
    under ``manager="reportingTxManager"`` lands on the secondary only."""
    ctx, engine_a, engine_b = _two_datasource_ctx(tmp_path)
    try:
        ctx.get(PrimaryWriter).add("primary-row")
        ctx.get(ReportingWriter).add("reporting-row")

        assert _names(engine_a) == ["primary-row"]
        assert _names(engine_b) == ["reporting-row"]
    finally:
        ctx.close()


def test_rollback_isolated_per_datasource(tmp_path: Path) -> None:
    """A rollback on the primary datasource leaves the secondary's
    committed rows untouched (the TSM binds sessions per sessionmaker)."""
    ctx, engine_a, engine_b = _two_datasource_ctx(tmp_path)
    try:
        ctx.get(ReportingWriter).add("keep")

        with pytest.raises(RuntimeError, match="boom"):
            ctx.get(PrimaryWriter).add_then_fail("rolled-back")

        assert _count(engine_a) == 0
        assert _names(engine_b) == ["keep"]
    finally:
        ctx.close()
