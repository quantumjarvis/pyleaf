"""M3 benchmark: ``CrudRepository.find_by_id`` vs raw ``Session.get``.

CI-visible version of the same-run ratio pin inside
``packages/pyleaf-data/tests/test_crud_repository.py``
(``test_find_by_id_overhead_vs_raw_session_get``): a repository bean
joining an ambient transaction must stay within 10% of the
``Session.get`` it delegates to. Both arms ``expire_all()`` + get the
same pk, so every iteration issues a real SELECT — the only delta on
the hot path is the repository's session resolution + delegation.

The absolute pin is deliberately generous (shared runners vary); the
same-run ratio is the real gate.
"""

from pathlib import Path

from pyleaf.core import ApplicationContext, Bean, Configuration
from pyleaf.data import CrudRepository
from pyleaf.starter.sqlalchemy.tx import (
    SqlAlchemyTransactionManager,
    current_session,
)
from pyleaf.tx import TransactionTemplate
from sqlalchemy import String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from webbench import median_per_call

RATIO_LIMIT = 1.10
ABSOLUTE_THRESHOLD_S = 500e-6


class _Base(DeclarativeBase):
    """Declarative base for the m3 repository benchmark table."""


class _Item(_Base):
    __tablename__ = "bench_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))


class _ItemRepo(CrudRepository[_Item, int], bean=True):
    """Repository bean for ``_Item`` (``bean=True`` marks the stereotype)."""


def _ctx(sm: sessionmaker) -> ApplicationContext:  # type: ignore[type-arg]
    """A refreshed context: sessionmaker + sync tx manager + the repo bean.

    Minimal port of ``packages/pyleaf-data/tests/data_ctx.py``: the repo
    resolves its ``SqlAlchemyTransactionManager`` by constructor
    injection, and its ops bind the ambient transaction via the TSM.
    """
    ctx = ApplicationContext()

    @Configuration
    class _DataConfig:
        @Bean
        def sessionfactory(self) -> sessionmaker:  # type: ignore[type-arg]
            return sm

        @Bean
        def txManager(
            self,
            sessionfactory: sessionmaker,  # type: ignore[type-arg]
        ) -> SqlAlchemyTransactionManager:
            return SqlAlchemyTransactionManager(sessionfactory)

    ctx.register(_DataConfig)
    ctx.register(_ItemRepo)
    ctx.refresh()
    return ctx


def test_find_by_id_overhead_vs_raw_session_get(
    benchmark, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/bench.db")
    _Base.metadata.create_all(engine)
    sm = sessionmaker(bind=engine)
    ctx = _ctx(sm)
    try:
        repo = ctx.get(_ItemRepo)
        manager = ctx.get(SqlAlchemyTransactionManager)
        with TransactionTemplate(manager).required():
            session = current_session(manager)
            saved = repo.save(_Item(name="bench"))
            session.flush()
            ident = int(saved.id)

            def raw_get() -> object:
                session.expire_all()
                return session.get(_Item, ident)

            def repo_get() -> object:
                session.expire_all()
                return repo.find_by_id(ident)

            # Warm both arms outside measurement (mapper/connection paths).
            assert raw_get() is not None
            assert repo_get() is not None

            raw_s = median_per_call(raw_get)
            assert benchmark(repo_get) is not None
            repo_s = benchmark.stats["median"]

            ratio = repo_s / raw_s
            benchmark.extra_info["raw_median_s"] = raw_s
            benchmark.extra_info["overhead_ratio"] = ratio
            assert ratio < RATIO_LIMIT, (
                f"find_by_id overhead {ratio:.3f}x exceeds {RATIO_LIMIT:.2f}x "
                f"(repo {repo_s * 1e6:.1f} us vs raw {raw_s * 1e6:.1f} us)"
            )
            assert repo_s < ABSOLUTE_THRESHOLD_S, (
                f"find_by_id {repo_s * 1e6:.1f} us exceeds "
                f"{ABSOLUTE_THRESHOLD_S * 1e6:.0f} us"
            )
    finally:
        ctx.close()
        engine.dispose()
