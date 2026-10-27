import pytest
from pyleaf.core import ApplicationContext, CircularDependency, Component


def test_direct_cycle_raises_error() -> None:
    """Test that A -> B -> A raises CircularDependency."""

    @Component
    class ServiceA:
        def __init__(self, service_b: "ServiceB"):
            pass

    @Component
    class ServiceB:
        def __init__(self, service_a: "ServiceA"):  # This creates a cycle
            pass

    ctx = ApplicationContext()
    ctx.register(ServiceA)
    ctx.register(ServiceB)

    # Should raise CircularDependency at refresh time
    with pytest.raises(CircularDependency, match=r"ServiceA.*ServiceB.*ServiceA"):
        ctx.refresh()


def test_indirect_cycle_raises_error() -> None:
    """Test that A -> B -> C -> A raises CircularDependency."""

    @Component
    class ServiceA:
        def __init__(self, service_b: "ServiceB"):
            pass

    @Component
    class ServiceB:
        def __init__(self, service_c: "ServiceC"):
            pass

    @Component
    class ServiceC:
        def __init__(self, service_a: "ServiceA"):  # This creates a cycle
            pass

    ctx = ApplicationContext()
    ctx.register(ServiceA)
    ctx.register(ServiceB)
    ctx.register(ServiceC)

    # Should raise CircularDependency at refresh time
    with pytest.raises(CircularDependency) as exc:
        ctx.refresh()
        # Relax the path-order assumption: the impl may report the cycle starting at any node
        error_msg = str(exc.value)
        assert "ServiceA" in error_msg
        assert "ServiceB" in error_msg
        assert "ServiceC" in error_msg


def test_self_cycle_raises_error() -> None:
    """Test that A -> A raises CircularDependency."""

    @Component
    class ServiceA:
        def __init__(self, service_a: "ServiceA"):  # Self cycle
            pass

    ctx = ApplicationContext()
    ctx.register(ServiceA)

    # Should raise CircularDependency at refresh time
    with pytest.raises(CircularDependency, match=r"ServiceA.*ServiceA"):
        ctx.refresh()


def test_diamond_is_not_cycle() -> None:
    """Test that a diamond (A -> B, A -> C, B -> D, C -> D) is NOT a cycle."""

    @Component
    class ServiceA:
        def __init__(self, service_b: "ServiceB", service_c: "ServiceC"):
            pass

    @Component
    class ServiceB:
        def __init__(self, service_d: "ServiceD"):
            pass

    @Component
    class ServiceC:
        def __init__(self, service_d: "ServiceD"):
            pass

    @Component
    class ServiceD:
        pass

    ctx = ApplicationContext()
    ctx.register(ServiceA)
    ctx.register(ServiceB)
    ctx.register(ServiceC)
    ctx.register(ServiceD)

    # Should not raise CircularDependency - it's a DAG (not a cycle)
    ctx.refresh()