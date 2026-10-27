from typing import Annotated

import pytest
from pyleaf.core import ApplicationContext, Component, NoUniqueBean, Primary, Qualifier


def test_two_impls_no_primary_raises_error() -> None:
    """Test that two impls of an interface + injection by bare type raises NoUniqueBean."""

    class ServiceInterface:
        def do_something(self) -> None:
            pass

    @Component
    class ImplA(ServiceInterface):
        def do_something(self) -> None:
            pass

    @Component
    class ImplB(ServiceInterface):
        def do_something(self) -> None:
            pass

    ctx = ApplicationContext()
    ctx.register(ImplA)
    ctx.register(ImplB)
    ctx.refresh()

    with pytest.raises(NoUniqueBean) as exc:
        ctx.get(ServiceInterface)
    assert "ImplA" in str(exc.value)
    assert "ImplB" in str(exc.value)


def test_primary_impl_wins() -> None:
    """Test that @Primary impl wins when there are multiple implementations."""

    class ServiceInterface:
        def do_something(self) -> None:
            pass

    @Component
    class ImplA(ServiceInterface):
        def do_something(self) -> None:
            pass

    @Component
    @Primary
    class PrimaryImpl(ServiceInterface):
        def do_something(self) -> None:
            pass

    ctx = ApplicationContext()
    ctx.register(ImplA)
    ctx.register(PrimaryImpl)
    ctx.refresh()

    # Should get the primary implementation
    service = ctx.get(ServiceInterface)
    assert isinstance(service, PrimaryImpl)


def test_annotated_with_qualifier_selects_named_bean() -> None:
    """Test that Qualifier selects the named bean even when other impl is @Primary."""

    class ServiceInterface:
        def do_something(self) -> None:
            pass

    @Component("implA")
    class ImplA(ServiceInterface):
        def do_something(self) -> None:
            pass

    @Component("implB")
    @Primary
    class PrimaryImpl(ServiceInterface):
        def do_something(self) -> None:
            pass

    @Component
    class Consumer:
        def __init__(self, dep: Annotated[ServiceInterface, Qualifier("implA")]):
            self.dep = dep

    ctx = ApplicationContext()
    ctx.register(ImplA)
    ctx.register(PrimaryImpl)
    ctx.register(Consumer)
    ctx.refresh()

    # Should get implA by qualifier, not the primary implementation
    consumer = ctx.get(Consumer)
    assert isinstance(consumer.dep, ImplA)


def test_annotated_with_qualifier_selects_non_primary() -> None:
    """Test that Qualifier selects a non-primary bean when there are multiple implementations."""

    class ServiceInterface:
        def do_something(self) -> None:
            pass

    @Component("implA")
    class ImplA(ServiceInterface):
        def do_something(self) -> None:
            pass

    @Component("implB")
    @Primary
    class PrimaryImpl(ServiceInterface):
        def do_something(self) -> None:
            pass

    ctx = ApplicationContext()
    ctx.register(ImplA)
    ctx.register(PrimaryImpl)
    ctx.refresh()

    # Should get the non-primary implementation when explicitly qualified
    service = ctx.get(Annotated[ServiceInterface, Qualifier("implB")])
    assert isinstance(service, PrimaryImpl)