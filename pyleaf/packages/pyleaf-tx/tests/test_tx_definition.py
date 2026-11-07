"""Pin TransactionDefinition defaults, immutability and the rollback rule."""

import dataclasses

import pytest
from pyleaf.tx import Isolation, Propagation, TransactionDefinition


def test_propagation_members() -> None:
    assert {p.name for p in Propagation} == {
        "REQUIRED",
        "REQUIRES_NEW",
        "NESTED",
        "SUPPORTS",
        "NOT_SUPPORTED",
        "MANDATORY",
        "NEVER",
    }


def test_isolation_members() -> None:
    assert {i.name for i in Isolation} == {
        "DEFAULT",
        "READ_UNCOMMITTED",
        "READ_COMMITTED",
        "REPEATABLE_READ",
        "SERIALIZABLE",
    }


def test_defaults() -> None:
    defn = TransactionDefinition()
    assert defn.propagation is Propagation.REQUIRED
    assert defn.isolation is Isolation.DEFAULT
    assert defn.read_only is False
    assert defn.timeout_s is None
    assert defn.rollback_for == (Exception,)
    assert defn.no_rollback_for == ()


def test_frozen() -> None:
    defn = TransactionDefinition()
    with pytest.raises(dataclasses.FrozenInstanceError):
        defn.read_only = True  # type: ignore[misc]


def test_rolls_back_on_any_exception_by_default() -> None:
    """Convention: any Exception subclass rolls back (broader than Spring's
    RuntimeException-only default)."""
    defn = TransactionDefinition()
    assert defn.rolls_back_on(RuntimeError("x"))
    assert defn.rolls_back_on(ValueError("x"))  # 'checked'-style errors roll back too


def test_never_rolls_back_on_base_exception() -> None:
    """BaseException-only failures (KeyboardInterrupt, SystemExit) do not roll
    back — they are not Exception subclasses."""
    defn = TransactionDefinition()
    assert not defn.rolls_back_on(KeyboardInterrupt())
    assert not defn.rolls_back_on(SystemExit())
    assert not defn.rolls_back_on(BaseException())


def test_no_rollback_for_excludes() -> None:
    defn = TransactionDefinition(no_rollback_for=(ValueError,))
    assert not defn.rolls_back_on(ValueError("x"))
    assert defn.rolls_back_on(TypeError("x"))
    assert defn.rolls_back_on(RuntimeError("x"))


def test_no_rollback_for_matches_subclasses() -> None:
    class MyValueError(ValueError):
        pass

    defn = TransactionDefinition(no_rollback_for=(ValueError,))
    assert not defn.rolls_back_on(MyValueError("x"))


def test_no_rollback_for_wins_over_rollback_for() -> None:
    defn = TransactionDefinition(
        rollback_for=(Exception,), no_rollback_for=(KeyError,)
    )
    assert not defn.rolls_back_on(KeyError("x"))
    assert defn.rolls_back_on(ValueError("x"))


def test_rollback_for_narrows_scope() -> None:
    defn = TransactionDefinition(rollback_for=(ValueError,))
    assert defn.rolls_back_on(ValueError("x"))
    assert not defn.rolls_back_on(TypeError("x"))
    assert not defn.rolls_back_on(Exception("x"))
