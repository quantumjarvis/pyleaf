"""RED acceptance tests for the non-@Around advice kinds (WI 1.4).

@Before/@After/@AfterReturning/@AfterThrowing are sugar compiled into
@Around-equivalent interceptors. Semantics pinned here:

- @Before runs before the method and cannot change its result.
- @AfterReturning runs only on the success path.
- @AfterThrowing runs only on the exception path; the error propagates.
- @After runs on both paths (finally semantics).
"""

import pytest
from pyleaf.core import (
    After,
    AfterReturning,
    AfterThrowing,
    ApplicationContext,
    Aspect,
    Before,
    Component,
    Invocation,
    bean_type,
)


class _Boom(Exception):
    pass


@Component
class _Failing:
    def ok(self) -> str:
        return "ok"

    def fail(self) -> str:
        raise _Boom("boom")


def _ctx(*classes: type) -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def test_before_runs_first_and_cannot_change_result() -> None:
    calls: list[str] = []

    @Aspect
    @Component
    class BeforeAspect:
        @Before(bean_type(_Failing))
        def before(self, inv: Invocation) -> None:
            calls.append(f"before:{inv.method_name}")

    ctx = _ctx(BeforeAspect, _Failing)
    bean = ctx.get(_Failing)

    assert bean.ok() == "ok"
    assert calls == ["before:ok"]


def test_after_returning_runs_only_on_success() -> None:
    calls: list[str] = []

    @Aspect
    @Component
    class AfterReturningAspect:
        @AfterReturning(bean_type(_Failing))
        def after_returning(self, inv: Invocation) -> None:
            calls.append(inv.method_name)

    ctx = _ctx(AfterReturningAspect, _Failing)
    bean = ctx.get(_Failing)

    assert bean.ok() == "ok"
    with pytest.raises(_Boom, match="boom"):
        bean.fail()

    assert calls == ["ok"]


def test_after_throwing_runs_only_on_exception_and_propagates() -> None:
    calls: list[str] = []

    @Aspect
    @Component
    class AfterThrowingAspect:
        @AfterThrowing(bean_type(_Failing))
        def after_throwing(self, inv: Invocation) -> None:
            calls.append(inv.method_name)

    ctx = _ctx(AfterThrowingAspect, _Failing)
    bean = ctx.get(_Failing)

    with pytest.raises(_Boom, match="boom"):
        bean.fail()
    assert bean.ok() == "ok"

    assert calls == ["fail"]


def test_after_runs_on_success_and_exception() -> None:
    calls: list[str] = []

    @Aspect
    @Component
    class AfterAspect:
        @After(bean_type(_Failing))
        def after(self, inv: Invocation) -> None:
            calls.append(inv.method_name)

    ctx = _ctx(AfterAspect, _Failing)
    bean = ctx.get(_Failing)

    assert bean.ok() == "ok"
    with pytest.raises(_Boom, match="boom"):
        bean.fail()

    assert calls == ["ok", "fail"]
