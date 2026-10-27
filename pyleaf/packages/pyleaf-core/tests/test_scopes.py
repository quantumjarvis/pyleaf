from pyleaf.core import ApplicationContext, Component, Lazy, Scope


def test_singleton_identity() -> None:
    """Test that singleton identity is preserved."""

    @Component
    class SingletonService:
        pass

    ctx = ApplicationContext()
    ctx.register(SingletonService)
    ctx.refresh()

    # Singletons should be the same instance
    service1 = ctx.get(SingletonService)
    service2 = ctx.get(SingletonService)
    assert service1 is service2


def test_prototype_distinct_objects() -> None:
    """Test that prototype produces distinct objects."""

    @Component(scope=Scope.PROTOTYPE)
    class PrototypeService:
        pass

    ctx = ApplicationContext()
    ctx.register(PrototypeService)
    ctx.refresh()

    # Prototypes should be different instances
    service1 = ctx.get(PrototypeService)
    service2 = ctx.get(PrototypeService)
    assert service1 is not service2


def test_lazy_default() -> None:
    """Test that lazy default works with instantiation counter."""

    @Component
    class InstantiationCounter:
        count = 0

        def __init__(self):
            InstantiationCounter.count += 1

    @Component
    class ServiceWithCounter:
        def __init__(self, counter: InstantiationCounter):
            pass

    # Reset counter
    InstantiationCounter.count = 0

    ctx = ApplicationContext()
    ctx.register(ServiceWithCounter)
    ctx.register(InstantiationCounter)

    # Counter should still be 0 before refresh/get
    assert InstantiationCounter.count == 0

    ctx.refresh()

    # Counter should still be 0 after refresh (lazy instantiation)
    assert InstantiationCounter.count == 0

    # Now get it, counter should increment
    ctx.get(ServiceWithCounter)
    assert InstantiationCounter.count == 1


def test_refresh_eager_instantiates_all_singletons() -> None:
    """Test that refresh(eager=True) instantiates all singletons."""

    @Component
    class SingletonCounter:
        count = 0

        def __init__(self):
            SingletonCounter.count += 1

    @Component
    class ServiceWithCounter:
        def __init__(self, counter: SingletonCounter):
            pass

    # Reset counter
    SingletonCounter.count = 0

    ctx = ApplicationContext()
    ctx.register(ServiceWithCounter)
    ctx.register(SingletonCounter)

    # Should instantiate nothing before eager refresh
    assert SingletonCounter.count == 0

    ctx.refresh(eager=True)

    # Should be instantiated with eager refresh
    assert SingletonCounter.count == 1


def test_lazy_false_instantiates_at_plain_refresh() -> None:
    """Test that @Lazy(False) singleton is instantiated at plain refresh."""

    @Component
    class EagerCounter:
        count = 0

        def __init__(self):
            EagerCounter.count += 1

    @Component
    @Lazy(False)
    class EagerService:
        def __init__(self, counter: EagerCounter):
            pass

    # Reset counter
    EagerCounter.count = 0

    ctx = ApplicationContext()
    ctx.register(EagerService)
    ctx.register(EagerCounter)

    # Should instantiate during refresh even without eager=True
    ctx.refresh()

    assert EagerCounter.count == 1