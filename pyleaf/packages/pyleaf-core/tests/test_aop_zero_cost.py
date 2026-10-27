"""RED acceptance test: unmatched beans pay zero cost (WI 1.4).

A bean whose methods match no pointcut gets NO attribute writes: dispatch
stays a plain bound method, nothing is patched onto the instance dict.
"""

import inspect
from typing import Any

from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Component,
    Invocation,
    bean_type,
)


def test_bean_with_no_matching_pointcut_is_untouched() -> None:
    records: list[str] = []

    @Component
    class PlainService:
        def method(self) -> str:
            return "plain"

    @Component
    class AdvisedService:
        def method(self) -> str:
            return "advised"

    @Aspect
    @Component
    class MatchingAspect:
        @Around(bean_type(AdvisedService))
        def wrap(self, inv: Invocation) -> Any:
            records.append(inv.method_name)
            return inv.proceed()

    ctx = ApplicationContext()
    ctx.register(MatchingAspect)
    ctx.register(PlainService)
    ctx.register(AdvisedService)
    ctx.refresh()

    plain = ctx.get(PlainService)
    advised = ctx.get(AdvisedService)

    # Nothing patched on the unmatched instance: dispatch is unmodified.
    assert "method" not in plain.__dict__
    assert inspect.ismethod(plain.method)
    # Sanity: interception machinery is active for the matched bean.
    assert "method" in advised.__dict__

    assert plain.method() == "plain"
    assert records == []
    assert advised.method() == "advised"
    assert records == ["method"]
