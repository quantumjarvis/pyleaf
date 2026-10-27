"""RED acceptance tests: interception preserves introspection (WI 1.4).

Intercepted methods keep their signature, name and docstring; the bean
remains an instance of its declared class. Descriptors and private methods
are never wrapped.
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


def test_signature_name_doc_and_identity_preserved() -> None:
    @Component
    class Target:
        def work(self, a: int, b: str = "x") -> str:
            """Do the work."""
            return f"{a}:{b}"

    @Aspect
    @Component
    class WrapAll:
        @Around(bean_type(Target))
        def wrap(self, inv: Invocation) -> Any:
            return inv.proceed()

    ctx = ApplicationContext()
    ctx.register(WrapAll)
    ctx.register(Target)
    ctx.refresh()

    bean = ctx.get(Target)
    assert isinstance(bean, Target)
    # Same signature as a pristine (unwrapped) bound method.
    assert inspect.signature(bean.work) == inspect.signature(Target().work)
    assert bean.work.__name__ == "work"
    assert bean.work.__doc__ == "Do the work."


def test_descriptors_and_private_methods_are_not_intercepted() -> None:
    records: list[str] = []

    @Component
    class Target:
        @property
        def prop(self) -> str:
            return "prop"

        @classmethod
        def make(cls) -> str:
            return "cls"

        @staticmethod
        def util() -> str:
            return "static"

        def _private(self) -> str:
            return "secret"

        def work(self) -> str:
            return "worked"

    @Aspect
    @Component
    class WrapAll:
        @Around(bean_type(Target))
        def wrap(self, inv: Invocation) -> Any:
            records.append(inv.method_name)
            return inv.proceed()

    ctx = ApplicationContext()
    ctx.register(WrapAll)
    ctx.register(Target)
    ctx.refresh()

    bean = ctx.get(Target)
    # None of these may be wrapped, even though bean_type(Target) would match.
    assert bean.prop == "prop"
    assert bean.make() == "cls"
    assert bean.util() == "static"
    assert bean._private() == "secret"
    assert records == []

    # Sanity: plain methods on the same bean ARE intercepted.
    assert bean.work() == "worked"
    assert records == ["work"]
