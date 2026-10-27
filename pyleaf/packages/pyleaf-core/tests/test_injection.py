from typing import Annotated, Protocol, runtime_checkable

from pyleaf.core import ApplicationContext, BeanCreationError, Component, Order, Qualifier


def test_concrete_dep_injection() -> None:
    """Test that concrete dep injection works."""

    @Component
    class ConcreteService:
        pass

    ctx = ApplicationContext()
    ctx.register(ConcreteService)
    ctx.refresh()

    # Should be able to get the bean
    service = ctx.get(ConcreteService)
    assert service is not None


def test_abc_dep_injection() -> None:
    """Test that ABC dep injection works."""

    from abc import ABC, abstractmethod

    class ServiceInterface(ABC):
        @abstractmethod
        def do_something(self) -> None:
            pass

    @Component
    class ConcreteService(ServiceInterface):
        def do_something(self) -> None:
            pass

    ctx = ApplicationContext()
    ctx.register(ConcreteService)
    ctx.refresh()

    # Should resolve to the concrete implementation
    service = ctx.get(ServiceInterface)
    assert isinstance(service, ConcreteService)


def test_runtime_checkable_protocol_dep_injection() -> None:
    """Test that runtime checkable protocol dep injection works."""

    @runtime_checkable
    class ServiceProtocol(Protocol):
        def do_something(self) -> None: ...

    @Component
    class ConcreteService:
        def do_something(self) -> None:
            pass

    ctx = ApplicationContext()
    ctx.register(ConcreteService)
    ctx.refresh()

    # Should resolve to the concrete implementation
    service = ctx.get(ServiceProtocol)
    assert isinstance(service, ConcreteService)

    # Add a consumer to prove injection works
    @Component
    class Consumer:
        def __init__(self, dep: ServiceProtocol):
            self.dep = dep

    ctx.register(Consumer)
    consumer = ctx.get(Consumer)  # Prove that injection works and not just get()
    assert isinstance(consumer.dep, ConcreteService)


def test_list_injection() -> None:
    """Test that list[T] injects all beans of T ordered by @Order."""

    from abc import ABC, abstractmethod

    class ServiceInterface(ABC):
        @abstractmethod
        def do_something(self) -> None:
            pass

    @Component
    @Order(2)
    class ServiceB(ServiceInterface):
        def do_something(self) -> None:
            pass

    @Component
    @Order(1)
    class ServiceA(ServiceInterface):
        def do_something(self) -> None:
            pass

    @Component
    class Consumer:
        def __init__(self, services: list[ServiceInterface]):
            self.services = services

    ctx = ApplicationContext()
    ctx.register(ServiceA)
    ctx.register(ServiceB)
    ctx.register(Consumer)
    ctx.refresh()

    # Should get all services in order
    consumer = ctx.get(Consumer)
    services = consumer.services
    assert len(services) == 2
    assert isinstance(services[0], ServiceA)  # First by order
    assert isinstance(services[1], ServiceB)


def test_optional_injection() -> None:
    """Test that T | None gives None when unregistered and bean when registered."""

    @Component
    class ConcreteService:
        pass

    @Component
    class Consumer:
        def __init__(self, service: ConcreteService | None):
            self.service = service

    ctx = ApplicationContext()
    ctx.register(ConcreteService)
    ctx.register(Consumer)
    ctx.refresh()

    # Test with optional where dependency is registered
    consumer = ctx.get(Consumer)
    assert consumer.service is not None

    # Test with optional where dependency is not registered
    @Component
    class AnotherService:
        pass

    @Component
    class AnotherConsumer:
        def __init__(self, service: AnotherService | None):
            self.service = service

    ctx.register(AnotherConsumer)

    another_consumer = ctx.get(AnotherConsumer)
    assert another_consumer.service is None


def test_annotated_injection() -> None:
    """Test that Annotated[T, Qualifier("name")] selects the named bean."""

    @Component("serviceA")
    class ServiceA:
        pass

    @Component("serviceB")
    class ServiceB(ServiceA):
        pass

    @Component
    class Consumer:
        def __init__(self, dep: Annotated[ServiceA, Qualifier("serviceB")]):
            self.dep = dep

    ctx = ApplicationContext()
    ctx.register(ServiceA)
    ctx.register(ServiceB)
    ctx.register(Consumer)
    ctx.refresh()

    # Should select by qualifier
    consumer = ctx.get(Consumer)
    assert isinstance(consumer.dep, ServiceB)


def test_missing_annotation_raises_error() -> None:
    """Test that a ctor param with no annotation raises BeanCreationError."""

    @Component
    class ServiceWithBadParam:
        def __init__(self, bad_param):  # No type annotation
            pass

    ctx = ApplicationContext()
    ctx.register(ServiceWithBadParam)

    # Should raise BeanCreationError during refresh
    import pytest
    with pytest.raises(BeanCreationError) as exc:
        ctx.refresh()
        assert "ServiceWithBadParam" in str(exc.value)
        assert "bad_param" in str(exc.value)