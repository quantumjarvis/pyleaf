"""Auto-configuration wiring for the transaction manager beans."""

from pathlib import Path
from typing import Any

import pytest
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Configuration,
    Environment,
    NoSuchBean,
    PropertySource,
)
from pyleaf.starter.sqlalchemy.autoconfigure import SqlAlchemyAutoConfiguration
from pyleaf.starter.sqlalchemy.tx import (
    AsyncSqlAlchemyTransactionManager,
    SqlAlchemyTransactionManager,
    current_session,
)
from pyleaf.tx import (
    AsyncTransactionManager,
    TransactionDefinition,
    TransactionManager,
    TransactionTemplate,
)
from sa_models import Base, TxItem
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


def _ctx(
    *classes: type, values: dict[str, object]
) -> ApplicationContext:
    ctx = ApplicationContext(
        environment=Environment(sources=[PropertySource("test", values)])
    )
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def _sqlite_url(tmp_path: Path) -> dict[str, object]:
    return {"app.datasource.url": f"sqlite:///{tmp_path}/app.db"}


def test_tm_bean_created_and_resolvable_by_protocol(tmp_path: Path) -> None:
    ctx = _ctx(SqlAlchemyAutoConfiguration, values=_sqlite_url(tmp_path))

    tm = ctx.get(SqlAlchemyTransactionManager)

    assert isinstance(tm, SqlAlchemyTransactionManager)
    assert ctx.get(TransactionManager) is tm
    assert ctx.get_bean("sqlalchemyTransactionManager") is tm


def test_tm_bean_drives_a_real_transaction(tmp_path: Path) -> None:
    ctx = _ctx(SqlAlchemyAutoConfiguration, values=_sqlite_url(tmp_path))
    engine = ctx.get(Engine)
    Base.metadata.create_all(engine)

    tm = ctx.get(TransactionManager)
    factory = ctx.get(sessionmaker)
    tmpl = TransactionTemplate(tm)
    tmpl.execute(
        lambda: current_session(factory).add(TxItem(name="auto"))
    )

    with factory() as check:
        assert list(check.scalars(select(TxItem.name)).all()) == ["auto"]


def test_tm_bean_absent_without_datasource_url() -> None:
    """No url → no engine → no sessionmaker → TM def dropped with reason."""
    ctx = _ctx(SqlAlchemyAutoConfiguration, values={})

    with pytest.raises(NoSuchBean):
        ctx.get_bean("sqlalchemyTransactionManager")

    entries = {
        e.bean_name: e for e in ctx.condition_report.entries
    }
    entry = entries["sqlalchemyTransactionManager"]
    assert entry.matched is False
    assert any("@ConditionalOnBean" in reason for reason in entry.reasons)


def test_user_transaction_manager_backs_off(tmp_path: Path) -> None:
    """A user-provided TransactionManager bean drops the starter's def."""

    class _UserTM:
        def begin(self, definition: TransactionDefinition) -> Any:
            raise NotImplementedError

        def commit(self, status: Any) -> None:
            raise NotImplementedError

        def rollback(self, status: Any) -> None:
            raise NotImplementedError

    @Configuration
    class UserTx:
        @Bean
        def userTransactionManager(self) -> _UserTM:
            return _UserTM()

    ctx = _ctx(
        UserTx, SqlAlchemyAutoConfiguration, values=_sqlite_url(tmp_path)
    )

    assert isinstance(ctx.get(TransactionManager), _UserTM)
    with pytest.raises(NoSuchBean):
        ctx.get_bean("sqlalchemyTransactionManager")


def test_async_tm_bean_when_async_sessionmaker_exists(tmp_path: Path) -> None:
    """The async manager bean is created when the user config provides an
    async_sessionmaker (the starter does not make async engines itself)."""
    pytest.importorskip("aiosqlite")
    from sqlalchemy.ext.asyncio import (
        async_sessionmaker,
        create_async_engine,
    )

    @Configuration
    class AsyncDb:
        @Bean
        def asyncSessionFactory(self) -> async_sessionmaker:
            return async_sessionmaker(
                bind=create_async_engine(
                    f"sqlite+aiosqlite:///{tmp_path}/async.db"
                )
            )

    ctx = _ctx(SqlAlchemyAutoConfiguration, AsyncDb, values={})

    tm = ctx.get(AsyncSqlAlchemyTransactionManager)
    assert isinstance(tm, AsyncSqlAlchemyTransactionManager)
    assert ctx.get(AsyncTransactionManager) is tm


def test_async_tm_bean_absent_without_async_sessionmaker(
    tmp_path: Path,
) -> None:
    """Sync-only config: no async manager def survives."""
    ctx = _ctx(SqlAlchemyAutoConfiguration, values=_sqlite_url(tmp_path))

    with pytest.raises(NoSuchBean):
        ctx.get_bean("sqlalchemyAsyncTransactionManager")
