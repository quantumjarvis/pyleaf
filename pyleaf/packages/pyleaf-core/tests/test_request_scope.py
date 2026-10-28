"""Request scope: contextvar scope, proxy semantics and ctx integration."""

import logging
import uuid

import pytest
from pyleaf.core import (
    ApplicationContext,
    Bean,
    CircularDependency,
    Component,
    Configuration,
    PostConstruct,
    PreDestroy,
    RequestScope,
    RequestScopedProxy,
    Scope,
    ScopeNotActive,
    Service,
)


class _Identified:
    """Bean helper: a unique id object per instance for identity checks."""

    def __init__(self) -> None:
        self.ident = uuid.uuid4().hex


def _ctx_with(*classes: type) -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def test_scope_enum_has_request() -> None:
    assert Scope.REQUEST in Scope


def test_current_raises_outside_scope() -> None:
    ctx = ApplicationContext()
    with pytest.raises(ScopeNotActive):
        RequestScope.current()
    scope = RequestScope(ctx)
    token = scope.activate()
    try:
        assert RequestScope.current() is scope
    finally:
        RequestScope.deactivate(token)
    with pytest.raises(ScopeNotActive):
        RequestScope.current()


def test_context_manager_activates_and_closes() -> None:
    destroyed: list[str] = []

    @Component(scope=Scope.REQUEST)
    class Req:
        @PreDestroy
        def bye(self) -> None:
            destroyed.append("x")

    ctx = _ctx_with(Req)
    proxy = ctx.get(Req)
    with RequestScope(ctx):
        _ = proxy.bye  # force creation inside the scope
        assert RequestScope.active() is not None
    assert destroyed == ["x"]
    assert RequestScope.active() is None


def test_ctx_get_returns_proxy_not_instance() -> None:
    @Component(scope=Scope.REQUEST)
    class Req(_Identified):
        pass

    ctx = _ctx_with(Req)
    bean = ctx.get(Req)
    assert isinstance(bean, RequestScopedProxy)
    # Pinned semantics: isinstance against the bean type is NOT supported.
    assert not isinstance(bean, Req)
    # One proxy per definition, shared by get() and get_bean().
    assert ctx.get(Req) is bean
    assert ctx.get_bean("req") is bean
    # Still a proxy inside an active scope — request beans are always proxied.
    with RequestScope(ctx):
        assert isinstance(ctx.get(Req), RequestScopedProxy)


def test_request_beans_not_instantiated_at_refresh() -> None:
    created = 0

    @Component(scope=Scope.REQUEST)
    class Req:
        def __init__(self) -> None:
            nonlocal created
            created += 1

    _ctx_with(Req)
    assert created == 0

    ctx = ApplicationContext()
    ctx.register(Req)
    ctx.refresh(eager=True)
    assert created == 0


def test_same_scope_same_instance_two_injection_points() -> None:
    @Component(scope=Scope.REQUEST)
    class Req(_Identified):
        pass

    @Service
    class Left:
        def __init__(self, req: Req) -> None:
            self.req = req

    @Service
    class Right:
        def __init__(self, req: Req) -> None:
            self.req = req

    ctx = _ctx_with(Req, Left, Right)
    with RequestScope(ctx):
        assert ctx.get(Left).req.ident == ctx.get(Right).req.ident
        assert ctx.get(Left).req.ident == ctx.get(Req).ident


def test_distinct_scopes_distinct_instances() -> None:
    @Component(scope=Scope.REQUEST)
    class Req(_Identified):
        pass

    ctx = _ctx_with(Req)
    proxy = ctx.get(Req)
    with RequestScope(ctx):
        first = proxy.ident
    with RequestScope(ctx):
        second = proxy.ident
    assert first != second


def test_nested_scopes_restore_outer() -> None:
    @Component(scope=Scope.REQUEST)
    class Req(_Identified):
        pass

    ctx = _ctx_with(Req)
    proxy = ctx.get(Req)
    with RequestScope(ctx) as outer:
        outer_id = proxy.ident
        with RequestScope(ctx) as inner:
            assert RequestScope.current() is inner
            assert proxy.ident != outer_id
        assert RequestScope.current() is outer
        assert proxy.ident == outer_id


def test_predestroy_runs_on_close_reverse_order() -> None:
    destroyed: list[str] = []

    @Component(scope=Scope.REQUEST)
    class First:
        @PreDestroy
        def bye(self) -> None:
            destroyed.append("first")

    @Component(scope=Scope.REQUEST)
    class Second:
        @PreDestroy
        def bye(self) -> None:
            destroyed.append("second")

    ctx = _ctx_with(First, Second)
    scope = RequestScope(ctx)
    with scope:
        _ = ctx.get(First).bye  # created first
        _ = ctx.get(Second).bye  # created second
    assert destroyed == ["second", "first"]


def test_predestroy_failure_isolated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    destroyed: list[str] = []

    @Component(scope=Scope.REQUEST)
    class Bad:
        @PreDestroy
        def bye(self) -> None:
            raise RuntimeError("boom")

    @Component(scope=Scope.REQUEST)
    class Good:
        @PreDestroy
        def bye(self) -> None:
            destroyed.append("good")

    ctx = _ctx_with(Bad, Good)
    with caplog.at_level(logging.ERROR, logger="pyleaf.core.scope"):
        with RequestScope(ctx):
            _ = ctx.get(Bad).bye  # force creation
            _ = ctx.get(Good).bye
    # The failing hook was logged, the other still ran, no exception escaped.
    assert destroyed == ["good"]
    assert "Error running @PreDestroy" in caplog.text


def test_ctx_close_does_not_destroy_request_instances() -> None:
    destroyed: list[str] = []

    @Component(scope=Scope.REQUEST)
    class Req:
        @PreDestroy
        def bye(self) -> None:
            destroyed.append("x")

    ctx = _ctx_with(Req)
    scope = RequestScope(ctx)
    token = scope.activate()
    try:
        _ = ctx.get(Req).bye  # create inside the scope
        ctx.close()
        assert destroyed == []
    finally:
        RequestScope.deactivate(token)
        scope.close()
    assert destroyed == ["x"]


def test_proxy_outside_scope_raises_naming_bean() -> None:
    @Component(scope=Scope.REQUEST)
    class RequestThing(_Identified):
        def hello(self) -> str:
            return "hi"

    ctx = _ctx_with(RequestThing)
    proxy = ctx.get(RequestThing)
    with pytest.raises(ScopeNotActive, match="requestThing"):
        _ = proxy.ident
    with pytest.raises(ScopeNotActive, match="requestThing"):
        proxy.hello()


def test_proxy_after_scope_close_raises() -> None:
    """Post-request 'background task' access sees ScopeNotActive."""

    @Component(scope=Scope.REQUEST)
    class Req(_Identified):
        pass

    ctx = _ctx_with(Req)
    proxy = ctx.get(Req)
    with RequestScope(ctx):
        _ = proxy.ident
    with pytest.raises(ScopeNotActive, match="'req'"):
        _ = proxy.ident


def test_proxy_forwards_get_set_method_repr() -> None:
    @Component(scope=Scope.REQUEST)
    class Req:
        def __init__(self) -> None:
            self.value = 1

        def double(self, n: int) -> int:
            return n * 2

    ctx = _ctx_with(Req)
    proxy = ctx.get(Req)
    with RequestScope(ctx):
        assert proxy.value == 1
        proxy.value = 41
        assert proxy.value == 41
        assert proxy.double(3) == 6
        assert repr(proxy) == repr(proxy._target())
        assert "Req" in repr(proxy)


def test_proxy_dunder_forwarding() -> None:
    @Component(scope=Scope.REQUEST)
    class Bag:
        def __init__(self) -> None:
            self.items = [1, 2, 3]

        def __len__(self) -> int:
            return len(self.items)

        def __contains__(self, item: object) -> bool:
            return item in self.items

        def __eq__(self, other: object) -> bool:
            return isinstance(other, Bag)

        def __hash__(self) -> int:
            return 7

    ctx = _ctx_with(Bag)
    proxy = ctx.get(Bag)
    with RequestScope(ctx):
        assert len(proxy) == 3
        assert 2 in proxy
        assert bool(proxy)
        assert hash(proxy) == 7
        # Two proxies for the same bean resolve to the same instance.
        assert proxy == ctx.get(Bag)


def test_singleton_sees_different_instance_per_request() -> None:
    seen: list[str] = []

    @Component(scope=Scope.REQUEST)
    class Req(_Identified):
        pass

    @Service
    class Holder:
        def __init__(self, req: Req) -> None:
            self.req = req
            # The injected dependency IS a proxy object.
            assert isinstance(req, RequestScopedProxy)

        def snapshot(self) -> str:
            return self.req.ident

    ctx = _ctx_with(Req, Holder)
    holder = ctx.get(Holder)
    for _ in range(3):
        with RequestScope(ctx):
            seen.append(holder.snapshot())
    assert len(set(seen)) == 3


def test_request_bean_dependencies_resolve_in_scope() -> None:
    @Service
    class Config:
        def url(self) -> str:
            return "db://x"

    @Component(scope=Scope.REQUEST)
    class Inner(_Identified):
        pass

    @Component(scope=Scope.REQUEST)
    class Outer:
        def __init__(self, inner: Inner, config: Config) -> None:
            self.inner = inner
            self.config = config

        def describe(self) -> str:
            return f"{self.inner.ident}:{self.config.url()}"

    ctx = _ctx_with(Config, Inner, Outer)
    outer = ctx.get(Outer)
    inner = ctx.get(Inner)
    with RequestScope(ctx):
        desc = outer.describe()
        assert desc.endswith(":db://x")
        assert desc.startswith(inner.ident)


def test_bean_method_request_scope() -> None:
    class Session(_Identified):
        pass

    @Configuration
    class Conf:
        @Bean(scope=Scope.REQUEST)
        def session(self) -> Session:
            return Session()

    ctx = _ctx_with(Conf)
    proxy = ctx.get(Session)
    assert isinstance(proxy, RequestScopedProxy)
    with RequestScope(ctx):
        assert proxy.ident == ctx.get(Session).ident


def test_request_bean_post_construct_and_bpp_run() -> None:
    ready: list[str] = []

    @Component(scope=Scope.REQUEST)
    class Req:
        def __init__(self) -> None:
            self.inited = False

        @PostConstruct
        def init(self) -> None:
            self.inited = True
            ready.append("x")

    ctx = _ctx_with(Req)
    proxy = ctx.get(Req)
    with RequestScope(ctx):
        assert proxy.inited is True
    assert ready == ["x"]


def test_cycle_detection_unaffected_by_request_scope() -> None:
    @Component(scope=Scope.REQUEST)
    class LoopA:
        def __init__(self, b: "LoopB") -> None:
            self.b = b

    @Component
    class LoopB:
        def __init__(self, a: LoopA) -> None:
            self.a = a

    ctx = ApplicationContext()
    ctx.register(LoopA)
    ctx.register(LoopB)
    with pytest.raises(CircularDependency):
        ctx.refresh()


def test_scope_instances_isolated_across_scopes() -> None:
    """Mutations in one scope never leak into the next."""

    @Component(scope=Scope.REQUEST)
    class Req:
        def __init__(self) -> None:
            self.value = 0

    ctx = _ctx_with(Req)
    proxy = ctx.get(Req)
    with RequestScope(ctx):
        proxy.value = 99
    with RequestScope(ctx):
        assert proxy.value == 0
