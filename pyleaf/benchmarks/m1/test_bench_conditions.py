"""M1 benchmark: two-phase condition evaluation over 500 conditional defs.

Mixes phase-A conditions (property presence/value) with a phase-B
bean-presence condition so both evaluation phases are exercised.
"""

from benchgen import make_component
from pyleaf.core import (
    ApplicationContext,
    ConditionalOnMissingBean,
    ConditionalOnProperty,
)

DEF_COUNT = 500
THRESHOLD_S = 0.100


class _Marker:
    """No bean is ever registered for this type."""


def _conditional_classes(count: int) -> list[type]:
    classes: list[type] = []
    for i in range(count):
        cls = make_component(f"CondBean{i}")
        if i % 3 == 0:
            # Phase A, matches: property missing but match_if_missing.
            cls = ConditionalOnProperty(f"bench.prop.{i}", match_if_missing=True)(cls)
        elif i % 3 == 1:
            # Phase B, matches: no _Marker bean exists.
            cls = ConditionalOnMissingBean(_Marker)(cls)
        else:
            # Phase A, dropped: required property value absent.
            cls = ConditionalOnProperty(f"bench.off.{i}", having_value="yes")(cls)
        classes.append(cls)
    return classes


CLASSES = _conditional_classes(DEF_COUNT)


def _run() -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in CLASSES:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def test_evaluate_500_conditional_definitions(benchmark) -> None:
    ctx = benchmark(_run)

    matched = sum(1 for e in ctx.condition_report.entries if e.matched)
    assert matched > 0
    assert benchmark.stats["median"] < THRESHOLD_S
