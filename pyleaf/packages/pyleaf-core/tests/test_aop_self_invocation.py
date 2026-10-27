"""RED acceptance test: self-invocation IS intercepted (WI 1.4).

Unlike proxy-based AOP (Spring), pyleaf patches methods on the instance, so
``self.internal()`` resolves through the instance dict and hits the wrapper.
"""

from typing import Any

from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Component,
    Invocation,
    bean_type,
)


def test_self_invocation_is_intercepted() -> None:
    calls: list[str] = []

    @Component
    class SelfCalling:
        def public(self) -> str:
            return "public->" + self.internal()

        def internal(self) -> str:
            return "internal"

    @Aspect
    @Component
    class CountingAspect:
        @Around(bean_type(SelfCalling))
        def wrap(self, inv: Invocation) -> Any:
            calls.append(inv.method_name)
            return inv.proceed()

    ctx = ApplicationContext()
    ctx.register(CountingAspect)
    ctx.register(SelfCalling)
    ctx.refresh()

    bean = ctx.get(SelfCalling)
    assert bean.public() == "public->internal"
    # Both the outer call and the self-invoked inner call are intercepted.
    assert calls == ["public", "internal"]

    assert bean.internal() == "internal"
    assert calls == ["public", "internal", "internal"]
