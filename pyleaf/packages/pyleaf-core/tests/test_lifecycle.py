from pyleaf.core import ApplicationContext, Component, PostConstruct, PreDestroy, Scope


def test_post_construct_runs_for_singleton() -> None:
    """Test that @PostConstruct runs once, after construction, per singleton."""

    @Component
    class Flagged:
        inits = 0

        def __init__(self) -> None:
            self.initialized = False

        @PostConstruct
        def init(self) -> None:
            self.initialized = True
            Flagged.inits += 1

    ctx = ApplicationContext()
    ctx.register(Flagged)
    ctx.refresh()

    service = ctx.get(Flagged)
    assert service.initialized
    # Resolving again returns the cached singleton; init is not re-run.
    assert ctx.get(Flagged) is service
    assert Flagged.inits == 1


def test_post_construct_runs_for_each_prototype_get() -> None:
    """Test that @PostConstruct runs on every prototype instance."""

    @Component(scope=Scope.PROTOTYPE)
    class ProtoFlagged:
        def __init__(self) -> None:
            self.initialized = False

        @PostConstruct
        def init(self) -> None:
            self.initialized = True

    ctx = ApplicationContext()
    ctx.register(ProtoFlagged)
    ctx.refresh()

    first = ctx.get(ProtoFlagged)
    second = ctx.get(ProtoFlagged)
    assert first is not second
    assert first.initialized
    assert second.initialized


def test_pre_destroy_runs_in_reverse_creation_order() -> None:
    """Test that close() runs @PreDestroy for singletons, last-created first."""

    destroyed: list[str] = []

    @Component
    class First:
        @PreDestroy
        def teardown(self) -> None:
            destroyed.append("first")

    @Component
    class Second:
        @PreDestroy
        def teardown(self) -> None:
            destroyed.append("second")

    ctx = ApplicationContext()
    ctx.register(First)
    ctx.register(Second)
    ctx.refresh()

    # Both beans are lazy: creation order is decided by the get() calls.
    ctx.get(First)
    ctx.get(Second)
    assert destroyed == []

    ctx.close()
    assert destroyed == ["second", "first"]


def test_pre_destroy_failure_does_not_stop_other_destroys() -> None:
    """Test that a throwing @PreDestroy does not abort the destroy chain."""

    destroyed: list[str] = []

    @Component
    class GoodBean:
        @PreDestroy
        def teardown(self) -> None:
            destroyed.append("good")

    @Component
    class BadBean:
        @PreDestroy
        def teardown(self) -> None:
            destroyed.append("bad")
            raise RuntimeError("teardown boom")

    ctx = ApplicationContext()
    ctx.register(GoodBean)
    ctx.register(BadBean)
    ctx.refresh()

    ctx.get(GoodBean)
    ctx.get(BadBean)

    # Reverse order: BadBean is destroyed first and throws; GoodBean's
    # teardown must still run and close() must not propagate the error.
    ctx.close()
    assert destroyed == ["bad", "good"]


def test_pre_destroy_not_run_for_prototypes() -> None:
    """Test that close() never invokes @PreDestroy on prototype beans."""

    destroyed: list[str] = []

    @Component(scope=Scope.PROTOTYPE)
    class ProtoBean:
        @PreDestroy
        def teardown(self) -> None:
            destroyed.append("proto")

    ctx = ApplicationContext()
    ctx.register(ProtoBean)
    ctx.refresh()

    ctx.get(ProtoBean)
    ctx.get(ProtoBean)
    ctx.close()
    assert destroyed == []


def test_close_on_fresh_context_is_noop() -> None:
    """Test that close() on a context that created nothing is a no-op."""

    ctx = ApplicationContext()
    ctx.close()


def test_close_with_uncreated_beans_is_noop() -> None:
    """Test that close() with only registered (never created) beans is a no-op."""

    @Component
    class NeverGot:
        pass

    ctx = ApplicationContext()
    ctx.register(NeverGot)
    ctx.refresh()
    ctx.close()
