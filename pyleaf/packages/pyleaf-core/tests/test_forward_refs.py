import sys
from pathlib import Path
from typing import Annotated

# Add tests directory to sys.path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_modules.forward_ref_module import (
    Dep,
    NamedDep,
    UsesDep,
    UsesList,
    UsesNamed,
    UsesOptional,
)
from pyleaf.core import ApplicationContext, Component, Qualifier


def test_forward_refs_injection() -> None:
    """Test that forward references work in injection points."""

    ctx = ApplicationContext()
    ctx.register(UsesDep)
    ctx.refresh()

    # Should be able to get the service without errors
    service = ctx.get(UsesDep)
    assert isinstance(service.dep, Dep)


def test_string_annotation_injection() -> None:
    """Test quoted annotations work: 'Dep'."""

    @Component
    class ServiceWithStringAnnotation:
        def __init__(self, dep: "Dep"):
            self.dep = dep

    ctx = ApplicationContext()
    ctx.register(Dep)
    ctx.register(ServiceWithStringAnnotation)
    ctx.refresh()

    # Should be able to get the service without errors
    service = ctx.get(ServiceWithStringAnnotation)
    assert isinstance(service.dep, Dep)


def test_list_string_annotation_injection() -> None:
    """Test list[quoted] annotations work: list['Dep']."""

    @Component
    class ServiceWithListAnnotation:
        def __init__(self, deps: list["Dep"]):
            self.deps = deps

    ctx = ApplicationContext()
    ctx.register(Dep)
    ctx.register(ServiceWithListAnnotation)
    ctx.refresh()

    # Should be able to get the service without errors
    service = ctx.get(ServiceWithListAnnotation)
    assert len(service.deps) == 1
    assert isinstance(service.deps[0], Dep)


def test_optional_string_annotation_injection() -> None:
    """Test Optional[quoted] annotations work: Optional['Dep']."""

    @Component
    class ServiceWithOptionalAnnotation:
        def __init__(self, dep: "Dep | None"):
            self.dep = dep

    ctx = ApplicationContext()
    ctx.register(Dep)
    ctx.register(ServiceWithOptionalAnnotation)
    ctx.refresh()

    # Should be able to get the service without errors
    service = ctx.get(ServiceWithOptionalAnnotation)
    assert isinstance(service.dep, Dep)


def test_annotated_string_annotation_injection() -> None:
    """Test Annotated[quoted, Qualifier('x')] annotations work."""

    @Component("namedDep")
    class NamedDep(Dep):
        pass

    @Component
    class ServiceWithAnnotatedRef:
        def __init__(self, dep: Annotated["Dep", Qualifier("namedDep")]):
            self.dep = dep

    ctx = ApplicationContext()
    ctx.register(NamedDep)
    ctx.register(ServiceWithAnnotatedRef)
    ctx.refresh()

    # Should be able to get the service without errors
    service = ctx.get(ServiceWithAnnotatedRef)
    assert isinstance(service.dep, NamedDep)


def test_fixture_module_integration() -> None:
    """Test that the fixture module classes work as expected."""
    
    # Test UsesDep - should inject Dep
    ctx = ApplicationContext()
    ctx.register(Dep)
    ctx.register(UsesDep)
    ctx.refresh()
    
    service = ctx.get(UsesDep)
    assert isinstance(service.dep, Dep)
    
    # Test UsesNamed - should inject NamedDep because of qualifier 
    ctx = ApplicationContext()
    ctx.register(NamedDep)
    ctx.register(UsesNamed)
    ctx.refresh()
    
    service = ctx.get(UsesNamed)
    assert isinstance(service.dep, NamedDep)  # Should be NamedDep due to qualifier
    
    # Test UsesList - should inject list of Dep beans 
    ctx = ApplicationContext()
    ctx.register(Dep)
    ctx.register(UsesList)
    ctx.refresh()
    
    service = ctx.get(UsesList)
    assert isinstance(service.deps, list)
    assert len(service.deps) == 1
    assert isinstance(service.deps[0], Dep)
    
    # Test UsesOptional - should inject None because OtherDep is not a component  
    ctx = ApplicationContext()
    ctx.register(UsesOptional)
    ctx.refresh()
    
    service = ctx.get(UsesOptional)
    assert service.dep is None