"""Edge-case tests for aop.py: compiled advice kinds (sync/async), the
@mark/@annotated marker convention, and advice discovery details."""

from typing import Any

import pytest
from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Before,
    Component,
    Invocation,
    PyleafError,
    annotated,
    bean_type,
    mark,
    name_matches,
)
from pyleaf.core.aop import compile_link, is_aspect, iter_advices
from pyleaf.core.stereotypes import COMPONENT_ATTR


class _Boom(Exception):
    pass


def _inv(*links: Any) -> Invocation:
    return Invocation(target=None, method_name="m", args=(), kwargs={}, links=links)


def test_pointcut_repr_is_its_description() -> None:
    assert repr(name_matches("w*")) == "name_matches('w*')"


def test_mark_records_marker_and_annotated_matches_it() -> None:
    @mark("tx")
    def handler() -> None:
        pass

    pc = annotated("tx")
    assert pc.matches(object, "handler", handler) is True

    def unmarked() -> None:
        pass

    assert pc.matches(object, "unmarked", unmarked) is False


def test_aspect_marks_plain_class_as_component() -> None:
    class Plain:
        pass

    result = Aspect(Plain)
    assert result is Plain
    assert is_aspect(Plain)
    assert getattr(Plain, COMPONENT_ATTR, None) is not None


def test_iter_advices_yields_overridden_advice_once() -> None:
    """A subclass advice overriding a base advice of the same name is yielded
    once, from the most-derived class."""
    pc = name_matches("*")

    @Aspect
    class BaseAsp:
        @Around(pc)
        def adv(self, inv: Invocation) -> Any:
            return inv.proceed()

    @Aspect
    class SubAsp(BaseAsp):
        @Around(pc)
        def adv(self, inv: Invocation) -> Any:
            return inv.proceed()

    advices = list(iter_advices(SubAsp))
    assert [name for name, _ in advices] == ["adv"]
    assert advices[0][1] is SubAsp.adv


def test_compile_link_unknown_kind_sync_raises() -> None:
    with pytest.raises(PyleafError, match="Unknown advice kind"):
        compile_link(lambda inv: None, "bogus", False)


def test_compile_link_unknown_kind_async_raises() -> None:
    with pytest.raises(PyleafError, match="Unknown advice kind"):
        compile_link(lambda inv: None, "bogus", True)


async def _ok(inv: Invocation) -> Any:
    return "done"


async def _fail(inv: Invocation) -> Any:
    raise _Boom("boom")


async def test_compile_async_before_runs_call_then_proceeds() -> None:
    calls: list[str] = []

    async def call(inv: Invocation) -> None:
        calls.append("in")

    link = compile_link(call, "before", True)
    assert await link(_inv(_ok)) == "done"
    assert calls == ["in"]


async def test_compile_async_after_runs_on_success_and_failure() -> None:
    calls: list[str] = []

    async def call(inv: Invocation) -> None:
        calls.append("after")

    link = compile_link(call, "after", True)
    assert await link(_inv(_ok)) == "done"
    with pytest.raises(_Boom):
        await link(_inv(_fail))
    assert calls == ["after", "after"]


async def test_compile_async_after_returning_runs_only_on_success() -> None:
    calls: list[str] = []

    async def call(inv: Invocation) -> None:
        calls.append("ar")

    link = compile_link(call, "after_returning", True)
    assert await link(_inv(_ok)) == "done"
    with pytest.raises(_Boom):
        await link(_inv(_fail))
    assert calls == ["ar"]


async def test_compile_async_after_throwing_runs_only_on_failure() -> None:
    calls: list[str] = []

    async def call(inv: Invocation) -> None:
        calls.append("at")

    link = compile_link(call, "after_throwing", True)
    assert await link(_inv(_ok)) == "done"
    with pytest.raises(_Boom):
        await link(_inv(_fail))
    assert calls == ["at"]


async def test_async_non_around_advice_intercepts_via_context() -> None:
    """End-to-end: an async @Before advice intercepts an async bean method."""
    calls: list[str] = []

    @Component
    class AsyncService:
        async def work(self) -> str:
            calls.append("method")
            return "ok"

    @Aspect
    @Component
    class AsyncBeforeAspect:
        @Before(bean_type(AsyncService))
        async def before(self, inv: Invocation) -> None:
            calls.append("before")

    ctx = ApplicationContext()
    ctx.register(AsyncBeforeAspect)
    ctx.register(AsyncService)
    ctx.refresh()

    bean = ctx.get(AsyncService)
    assert await bean.work() == "ok"
    assert calls == ["before", "method"]
