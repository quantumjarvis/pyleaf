"""RED acceptance tests for interceptor chain ordering via @Order (WI 1.4).

Lower @Order means OUTERMOST: the advice runs first on the way in and last
on the way out. @Order on the aspect class applies to all its advices;
@Order on the advice method overrides it for that advice.
"""

from typing import Any

from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Component,
    Invocation,
    Order,
    bean_type,
)


def test_lower_order_aspect_is_outermost() -> None:
    calls: list[str] = []

    @Component
    class ChainTarget:
        def work(self) -> str:
            calls.append("method")
            return "done"

    @Aspect
    @Component
    @Order(1)
    class OuterAspect:
        @Around(bean_type(ChainTarget))
        def wrap(self, inv: Invocation) -> Any:
            calls.append("outer-in")
            result = inv.proceed()
            calls.append("outer-out")
            return result

    @Aspect
    @Component
    @Order(5)
    class InnerAspect:
        @Around(bean_type(ChainTarget))
        def wrap(self, inv: Invocation) -> Any:
            calls.append("inner-in")
            result = inv.proceed()
            calls.append("inner-out")
            return result

    ctx = ApplicationContext()
    # Registered in reverse order to prove @Order, not registration, decides.
    ctx.register(InnerAspect)
    ctx.register(OuterAspect)
    ctx.register(ChainTarget)
    ctx.refresh()

    assert ctx.get(ChainTarget).work() == "done"
    assert calls == ["outer-in", "inner-in", "method", "inner-out", "outer-out"]


def test_advice_method_order_overrides_aspect_class_order() -> None:
    calls: list[str] = []

    @Component
    class ChainTarget:
        def work(self) -> str:
            calls.append("method")
            return "done"

    @Aspect
    @Component
    @Order(1)
    class OverriddenAspect:
        """Class-level order 1, but the advice below opts out with @Order(9)."""

        @Around(bean_type(ChainTarget))
        @Order(9)
        def wrap(self, inv: Invocation) -> Any:
            calls.append("overridden-in")
            result = inv.proceed()
            calls.append("overridden-out")
            return result

    @Aspect
    @Component
    @Order(5)
    class PlainAspect:
        @Around(bean_type(ChainTarget))
        def wrap(self, inv: Invocation) -> Any:
            calls.append("plain-in")
            result = inv.proceed()
            calls.append("plain-out")
            return result

    ctx = ApplicationContext()
    ctx.register(OverriddenAspect)
    ctx.register(PlainAspect)
    ctx.register(ChainTarget)
    ctx.refresh()

    ctx.get(ChainTarget).work()

    # Effective orders: plain=5, overridden=9 -> plain is outermost.
    assert calls == [
        "plain-in",
        "overridden-in",
        "method",
        "overridden-out",
        "plain-out",
    ]


def test_invocation_exposes_call_details() -> None:
    seen: dict[str, Any] = {}

    @Component
    class DetailTarget:
        def work(self, a: int, b: str = "x") -> str:
            return f"{a}:{b}"

    @Aspect
    @Component
    class SpyAspect:
        @Around(bean_type(DetailTarget))
        def wrap(self, inv: Invocation) -> Any:
            seen["target"] = inv.target
            seen["method_name"] = inv.method_name
            seen["args"] = inv.args
            seen["kwargs"] = inv.kwargs
            return inv.proceed()

    ctx = ApplicationContext()
    ctx.register(SpyAspect)
    ctx.register(DetailTarget)
    ctx.refresh()

    bean = ctx.get(DetailTarget)
    assert bean.work(7, b="y") == "7:y"
    assert seen == {
        "target": bean,
        "method_name": "work",
        "args": (7,),
        "kwargs": {"b": "y"},
    }
