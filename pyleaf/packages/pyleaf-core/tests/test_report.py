"""Tests for the condition evaluation report exposed on the context."""

from typing import Any

import pytest
from pyleaf.core import (
    ApplicationContext,
    Component,
    ConditionalOnClass,
    ConditionalOnProperty,
    Environment,
    NoSuchBean,
    PropertySource,
)


def _ctx(*classes: type, env: Environment | None = None) -> ApplicationContext:
    ctx = (
        ApplicationContext(environment=env)
        if env is not None
        else ApplicationContext()
    )
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def _entries_by_name(report: Any) -> dict[str, Any]:
    return {e.bean_name: e for e in report.entries}


def test_condition_report_exists_after_refresh() -> None:
    @Component
    class PlainBean:
        pass

    ctx = _ctx(PlainBean)
    assert ctx.condition_report is not None


def test_report_covers_matched_and_unmatched_defs() -> None:
    @Component
    @ConditionalOnClass("json")
    class MatchedBean:
        pass

    @Component
    @ConditionalOnClass("nonexistent_module_xyz")
    class UnmatchedBean:
        pass

    ctx = _ctx(MatchedBean, UnmatchedBean)
    entries = _entries_by_name(ctx.condition_report)
    assert entries["matchedBean"].matched is True
    assert entries["unmatchedBean"].matched is False
    with pytest.raises(NoSuchBean):
        ctx.get_bean("unmatchedBean")


def test_unmatched_entry_reason_mentions_condition_and_cause() -> None:
    @Component
    @ConditionalOnProperty("app.mode", having_value="prod")
    class ConditionalBean:
        pass

    env = Environment(sources=[PropertySource("test", {"app.mode": "dev"})])
    ctx = _ctx(ConditionalBean, env=env)
    entry = _entries_by_name(ctx.condition_report)["conditionalBean"]
    assert entry.matched is False
    reasons = " ".join(entry.reasons)
    assert "ConditionalOnProperty" in reasons
    assert "app.mode" in reasons
    assert "prod" in reasons
    assert "dev" in reasons


def test_matched_entry_also_records_reasons() -> None:
    @Component
    @ConditionalOnClass("json")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean)
    entry = _entries_by_name(ctx.condition_report)["conditionalBean"]
    assert entry.matched is True
    assert entry.reasons
    assert "ConditionalOnClass" in " ".join(entry.reasons)


def test_unconditional_defs_do_not_appear_in_report() -> None:
    @Component
    class PlainBean:
        pass

    @Component
    @ConditionalOnClass("json")
    class ConditionalBean:
        pass

    ctx = _ctx(PlainBean, ConditionalBean)
    entries = _entries_by_name(ctx.condition_report)
    assert "conditionalBean" in entries
    assert "plainBean" not in entries


def test_report_entry_count_covers_only_conditional_defs() -> None:
    """3 conditional beans (1 match, 2 fail for different reasons) + 1
    unconditional -> exactly 3 report entries."""

    @Component
    @ConditionalOnProperty("app.mode", having_value="prod")
    class MatchedBean:
        pass

    @Component
    @ConditionalOnProperty("app.mode", having_value="dev")
    class WrongValueBean:
        pass

    @Component
    @ConditionalOnClass("nonexistent_module_xyz")
    class MissingClassBean:
        pass

    @Component
    class PlainBean:
        pass

    env = Environment(sources=[PropertySource("test", {"app.mode": "prod"})])
    ctx = _ctx(MatchedBean, WrongValueBean, MissingClassBean, PlainBean, env=env)

    report = ctx.condition_report
    assert len(report.entries) == 3
    entries = _entries_by_name(report)
    assert set(entries) == {"matchedBean", "wrongValueBean", "missingClassBean"}
    assert entries["matchedBean"].matched is True
    assert entries["wrongValueBean"].matched is False
    assert entries["missingClassBean"].matched is False
