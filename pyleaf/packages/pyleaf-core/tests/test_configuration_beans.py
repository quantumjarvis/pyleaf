import pytest
from pyleaf.core import ApplicationContext, Bean, BeanCreationError, Component, Configuration


def test_configuration_bean_method_returns_type() -> None:
    """Test that @Bean method return type = bean type."""

    class ServiceA:
        pass

    @Configuration
    class ConfigClass:
        @Bean
        def service_bean(self) -> ServiceA:
            return ServiceA()

    ctx = ApplicationContext()
    ctx.register(ConfigClass)
    ctx.refresh()

    # Should be able to get the bean by its type
    service = ctx.get(ServiceA)
    assert isinstance(service, ServiceA)


def test_configuration_bean_method_name() -> None:
    """Test that @Bean method name = bean name."""

    class ServiceA:
        pass

    @Configuration
    class ConfigClass:
        @Bean(name="custom_service")
        def create_service(self) -> ServiceA:
            return ServiceA()

    ctx = ApplicationContext()
    ctx.register(ConfigClass)
    ctx.refresh()

    # Should be able to get the bean by its custom name
    service = ctx.get_bean("custom_service")
    assert isinstance(service, ServiceA)


def test_configuration_bean_method_params_injected() -> None:
    """Test that @Bean method params are injected."""

    @Component
    class DepA:
        pass

    class ServiceA:
        def __init__(self, dep_a: DepA):
            self.dep_a = dep_a

    @Configuration
    class ConfigClass:
        @Bean
        def service_bean(self, dep_a: DepA) -> ServiceA:
            return ServiceA(dep_a)

    ctx = ApplicationContext()
    ctx.register(DepA)
    ctx.register(ConfigClass)
    ctx.refresh()

    # Should be able to get the bean and it should have its dependencies injected
    service = ctx.get(ServiceA)
    assert isinstance(service.dep_a, DepA)


def test_configuration_bean_method_throws_raises_error() -> None:
    """Test that throwing @Bean method raises BeanCreationError."""

    class ServiceA:
        pass

    @Configuration
    class ConfigClass:
        @Bean
        def throwing_bean(self) -> ServiceA:
            raise RuntimeError("Test exception")

    ctx = ApplicationContext()
    ctx.register(ConfigClass)

    # Should raise BeanCreationError when trying to create the bean
    with pytest.raises(BeanCreationError) as exc:
        ctx.refresh()
    assert "throwing_bean" in str(exc.value)
    assert "RuntimeError" in str(exc.value)