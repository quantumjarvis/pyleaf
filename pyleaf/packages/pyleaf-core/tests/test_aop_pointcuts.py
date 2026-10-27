"""RED acceptance tests for the AOP pointcut DSL (WI 1.4).

Each pointcut constructor is exercised as a truth table: methods that should
match get intercepted (recorded by the advice), methods that should not match
pass straight through.
"""

from typing import Any

from fixture_aop.services import PeerService, TargetService
from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Component,
    Invocation,
    annotated,
    bean_type,
    name_matches,
    within,
)


def Tracked(func: Any) -> Any:
    """Marker decorator following the ``__pyleaf_markers__`` convention."""
    markers = getattr(func, "__pyleaf_markers__", set())
    func.__pyleaf_markers__ = markers | {Tracked}
    return func


def _recording_aspect(pointcut: Any, records: list[str]) -> type[Any]:
    """Build an @Aspect component whose @Around advice records method names."""

    @Aspect
    @Component
    class Recorder:
        @Around(pointcut)
        def wrap(self, inv: Invocation) -> Any:
            records.append(inv.method_name)
            return inv.proceed()

    return Recorder


def _ctx_with(
    pointcut: Any,
    records: list[str],
    *beans: type[Any],
) -> ApplicationContext:
    ctx = ApplicationContext()
    ctx.register(_recording_aspect(pointcut, records))
    for bean_cls in beans:
        ctx.register(bean_cls)
    ctx.refresh()
    return ctx


def test_within_matches_beans_by_module_prefix() -> None:
    records: list[str] = []

    @Component
    class OutsiderService:
        def find_outside(self) -> str:
            return "out"

    ctx = _ctx_with(
        within("fixture_aop"),
        records,
        TargetService,
        PeerService,
        OutsiderService,
    )

    ctx.get(TargetService).work()
    ctx.get(PeerService).find_peer()
    # Lives in this test module, not under fixture_aop -> not intercepted.
    ctx.get(OutsiderService).find_outside()

    assert records == ["work", "find_peer"]


def test_name_matches_globs_method_names() -> None:
    records: list[str] = []
    ctx = _ctx_with(name_matches("find_*"), records, TargetService)

    svc = ctx.get(TargetService)
    assert svc.find_one() == "one"
    assert svc.find_all() == ["all"]
    assert svc.work() == "worked"
    assert svc.helper() == "helped"

    assert records == ["find_one", "find_all"]


def test_bean_type_matches_exact_type() -> None:
    records: list[str] = []
    ctx = _ctx_with(bean_type(TargetService), records, TargetService, PeerService)

    ctx.get(TargetService).work()
    ctx.get(PeerService).work()

    assert records == ["work"]


def test_bean_type_matches_subclass_beans() -> None:
    records: list[str] = []

    @Component
    class SubService(TargetService):
        pass

    ctx = _ctx_with(bean_type(TargetService), records, SubService)

    ctx.get(SubService).work()

    assert records == ["work"]


def test_annotated_matches_marked_methods() -> None:
    records: list[str] = []

    @Component
    class TaggedService:
        @Tracked
        def marked(self) -> str:
            return "yes"

        def unmarked(self) -> str:
            return "no"

    ctx = _ctx_with(annotated(Tracked), records, TaggedService)

    svc = ctx.get(TaggedService)
    assert svc.marked() == "yes"
    assert svc.unmarked() == "no"

    assert records == ["marked"]


def test_and_combinator_requires_both() -> None:
    records: list[str] = []

    @Component
    class OutsiderService:
        def find_outside(self) -> str:
            return "out"

    pointcut = within("fixture_aop") & name_matches("find_*")
    ctx = _ctx_with(pointcut, records, TargetService, PeerService, OutsiderService)

    svc = ctx.get(TargetService)
    svc.find_one()  # within + name -> intercepted
    svc.work()  # within only -> skipped
    ctx.get(PeerService).find_peer()  # within + name -> intercepted
    ctx.get(OutsiderService).find_outside()  # name only -> skipped

    assert records == ["find_one", "find_peer"]


def test_or_combinator_matches_either() -> None:
    records: list[str] = []
    pointcut = name_matches("find_one") | name_matches("helper")
    ctx = _ctx_with(pointcut, records, TargetService)

    svc = ctx.get(TargetService)
    svc.find_one()
    svc.helper()
    svc.work()
    svc.find_all()

    assert records == ["find_one", "helper"]


def test_not_combinator_inverts_the_pointcut() -> None:
    records: list[str] = []
    ctx = _ctx_with(~name_matches("find_*"), records, TargetService)

    svc = ctx.get(TargetService)
    svc.work()
    svc.helper()
    svc.find_one()
    svc.find_all()

    assert records == ["work", "helper"]
