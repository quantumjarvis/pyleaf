"""Sync ``SqlAlchemyTransactionManager`` contract tests (sqlite paths)."""

import pytest
from pyleaf.starter.sqlalchemy.tx import (
    SqlAlchemyTransactionManager,
    current_session,
)
from pyleaf.tx import (
    NoTransactionError,
    Propagation,
    TransactionDefinition,
    TransactionManager,
    TransactionTemplate,
    UnexpectedRollbackError,
)
from sa_models import TxItem
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

_NESTED = TransactionDefinition(propagation=Propagation.NESTED)


def _names(factory: sessionmaker) -> list[str]:  # type: ignore[type-arg]
    with factory() as check:
        return sorted(check.scalars(select(TxItem.name)).all())


def test_manager_satisfies_transaction_manager_protocol(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    assert isinstance(manager, TransactionManager)


def test_required_commit_persists(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    tmpl = TransactionTemplate(manager)

    tmpl.execute(
        lambda: current_session(sqlite_sessionmaker).add(TxItem(name="kept"))
    )

    assert _names(sqlite_sessionmaker) == ["kept"]


def test_required_rollback_discards(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    tmpl = TransactionTemplate(manager)

    def body() -> None:
        current_session(sqlite_sessionmaker).add(TxItem(name="dropped"))
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        tmpl.execute(body)

    assert _names(sqlite_sessionmaker) == []


def test_joined_required_boundary_shares_session(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    tmpl = TransactionTemplate(manager)
    seen: list[Session] = []

    def body() -> None:
        seen.append(current_session(sqlite_sessionmaker))
        # A nested REQUIRED boundary joins: same bound session.
        TransactionTemplate(manager).execute(
            lambda: seen.append(current_session(sqlite_sessionmaker))
        )

    tmpl.execute(body)

    assert len(seen) == 2
    assert seen[0] is seen[1]


def test_current_session_resolves_by_manager_or_factory(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    tmpl = TransactionTemplate(manager)

    def body() -> None:
        assert current_session(manager) is current_session(sqlite_sessionmaker)
        assert isinstance(current_session(manager), Session)

    tmpl.execute(body)


def test_current_session_unbound_raises_no_transaction(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    with pytest.raises(NoTransactionError):
        current_session(sqlite_sessionmaker)


def test_current_session_require_tx_false_returns_none(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    assert current_session(sqlite_sessionmaker, require_tx=False) is None


def test_nested_inner_rollback_keeps_outer_row(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    """sqlite supports SAVEPOINT: inner rollback, outer commits."""
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    outer = TransactionTemplate(manager)
    nested = TransactionTemplate(manager, _NESTED)

    def body() -> None:
        current_session(sqlite_sessionmaker).add(TxItem(name="outer"))

        def inner() -> None:
            current_session(sqlite_sessionmaker).add(TxItem(name="inner"))
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            nested.execute(inner)

    outer.execute(body)

    assert _names(sqlite_sessionmaker) == ["outer"]


def test_nested_commit_then_outer_rollback_loses_both(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    outer = TransactionTemplate(manager)
    nested = TransactionTemplate(manager, _NESTED)

    def body() -> None:
        current_session(sqlite_sessionmaker).add(TxItem(name="outer"))
        nested.execute(
            lambda: current_session(sqlite_sessionmaker).add(TxItem(name="inner"))
        )
        raise ValueError("outer")

    with pytest.raises(ValueError, match="outer"):
        outer.execute(body)

    assert _names(sqlite_sessionmaker) == []


def test_joined_rollback_only_fails_outer_commit(
    sqlite_sessionmaker: sessionmaker,  # type: ignore[type-arg]
) -> None:
    """A joined inner boundary's failure marks the shared tx rollback-only:
    the outer commit then rolls back and raises UnexpectedRollbackError."""
    manager = SqlAlchemyTransactionManager(sqlite_sessionmaker)
    outer = TransactionTemplate(manager)
    inner = TransactionTemplate(manager)

    def body() -> None:
        current_session(sqlite_sessionmaker).add(TxItem(name="outer"))

        def failing() -> None:
            raise ValueError("inner")

        with pytest.raises(ValueError, match="inner"):
            inner.execute(failing)

    with pytest.raises(UnexpectedRollbackError):
        outer.execute(body)

    assert _names(sqlite_sessionmaker) == []
