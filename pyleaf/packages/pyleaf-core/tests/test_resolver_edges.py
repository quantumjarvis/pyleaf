"""Edge-case tests for resolver.py: hint-resolution error paths and
DepSpec classification branches not reachable through the happy-path
context tests."""

from typing import TYPE_CHECKING, Union

import pytest
from pyleaf.core.definition import BeanDefinition, BeanRegistry
from pyleaf.core.errors import BeanCreationError, CircularDependency
from pyleaf.core.resolver import (
    DepKind,
    DepSpec,
    bean_return_type,
    callable_dependencies,
    detect_cycles,
)

if TYPE_CHECKING:
    # Referenced only by string annotations in the error-path tests below:
    # defined for the linter, but absent at runtime so get_type_hints/eval
    # hit the intended NameError branches.
    class Missing:
        pass


class _A:
    pass


class _B:
    pass


_UNION_AB = _A | _B
_OPT_UNION_AB = _A | _B | None
# A union whose ForwardRef resolves to a non-type (literal int): get_type_hints
# resolves it fine; _classify then fails building `int | 3`.
_UNION_WITH_NON_TYPE = Union[int, "3", None]


def test_varargs_and_kwargs_params_are_skipped() -> None:
    def f(x: _A, *args: _B, **kwargs: _B) -> None:
        pass

    specs = callable_dependencies(f, "f", None)
    assert specs == [DepSpec("x", DepKind.SINGLE, _A)]


def test_union_without_none_is_single() -> None:
    def f(x: _UNION_AB) -> None:
        pass

    spec = callable_dependencies(f, "f", None)[0]
    assert spec.kind is DepKind.SINGLE
    assert spec.bean_type == _UNION_AB


def test_optional_multi_union_collapses_inner_union() -> None:
    def f(x: _OPT_UNION_AB) -> None:
        pass

    spec = callable_dependencies(f, "f", None)[0]
    assert spec.kind is DepKind.OPTIONAL
    assert spec.bean_type == (_A | _B)


def test_hint_eval_type_error_raises_bean_creation_error() -> None:
    def f(x: "int | 3") -> None:
        pass

    with pytest.raises(BeanCreationError, match="Cannot inspect type hints of f"):
        callable_dependencies(f, "f", None)


def test_unresolvable_param_hint_reports_param() -> None:
    def f(x: "Missing") -> None:
        pass

    with pytest.raises(
        BeanCreationError, match="Cannot resolve type hint for parameter 'x' of f"
    ):
        callable_dependencies(f, "f", None)


def test_unresolvable_return_hint_reports_owner() -> None:
    """Params resolve cleanly but the return annotation does not: the generic
    'Cannot resolve type hints' error is raised after the per-param scan."""

    def f(x: _A) -> "Missing":
        pass

    with pytest.raises(BeanCreationError, match="Cannot resolve type hints of f"):
        callable_dependencies(f, "f", None)


def test_name_error_fallback_resolves_via_registry() -> None:
    """A class defined inside a function is invisible to the test module's
    globals; the registry-localns fallback resolves it by name."""

    class Dep:
        pass

    def f(x: "Dep") -> None:
        pass

    reg = BeanRegistry()
    reg.register(BeanDefinition(name="dep", bean_type=Dep, factory=Dep))

    specs = callable_dependencies(f, "f", reg)
    assert specs == [DepSpec("x", DepKind.SINGLE, Dep)]


def test_class_callable_evals_remaining_str_param_hints() -> None:
    """get_type_hints on a class returns class-level hints, so __init__ params
    fall back to param.annotation; surviving strings are eval'd against the
    registry namespace (classes have no __globals__)."""

    class Dep:
        pass

    class Cls:
        def __init__(self, x: "Dep") -> None:
            pass

    reg = BeanRegistry()
    reg.register(BeanDefinition(name="dep", bean_type=Dep, factory=Dep))

    specs = callable_dependencies(Cls, "Cls", reg)
    assert specs == [DepSpec("x", DepKind.SINGLE, Dep)]


def test_class_callable_unresolvable_str_hint_raises() -> None:
    class Cls:
        def __init__(self, x: "Missing") -> None:
            pass

    with pytest.raises(
        BeanCreationError, match="Cannot resolve type hint for parameter 'x' of Cls"
    ):
        callable_dependencies(Cls, "Cls", None)


def test_unclassifiable_hint_raises() -> None:
    def f(x: _UNION_WITH_NON_TYPE) -> None:
        pass

    with pytest.raises(
        BeanCreationError, match=r"Cannot classify type hint .* for parameter 'x'"
    ):
        callable_dependencies(f, "f", None)


def test_bean_return_type_requires_return_annotation() -> None:
    def f(x: _A):  # deliberately no return annotation for the error path
        pass

    with pytest.raises(
        BeanCreationError, match="@Bean method 'f' has no return type annotation"
    ):
        bean_return_type(f, "f", None)


def test_detect_cycles_prefers_single_primary_candidate() -> None:
    """Multiple candidates for a dep with exactly one primary: the static graph
    edge points at the primary definition only."""

    class Base:
        pass

    class Impl1(Base):
        pass

    class Impl2(Base):
        pass

    class Svc:
        pass

    reg = BeanRegistry()
    reg.register(
        BeanDefinition(
            name="svc",
            bean_type=Svc,
            factory=Svc,
            dep_specs=lambda: [DepSpec("dep", DepKind.SINGLE, Base)],
        )
    )
    reg.register(BeanDefinition(name="impl1", bean_type=Impl1, factory=Impl1))
    reg.register(
        BeanDefinition(name="impl2", bean_type=Impl2, factory=Impl2, primary=True)
    )

    detect_cycles(reg)  # impl2 is the only static edge target; no cycle


def test_detect_cycles_ambiguous_candidates_expand_all() -> None:
    """Multiple candidates with no primary: every candidate is an edge target;
    a cycle through any of them is reported."""

    class Base:
        pass

    class Impl1(Base):
        pass

    class Impl2(Base):
        pass

    class Svc:
        pass

    reg = BeanRegistry()
    reg.register(
        BeanDefinition(
            name="svc",
            bean_type=Svc,
            factory=Svc,
            dep_specs=lambda: [DepSpec("dep", DepKind.SINGLE, Base)],
        )
    )
    reg.register(
        BeanDefinition(
            name="impl1",
            bean_type=Impl1,
            factory=Impl1,
            dep_specs=lambda: [DepSpec("svc", DepKind.SINGLE, Svc)],
        )
    )
    reg.register(BeanDefinition(name="impl2", bean_type=Impl2, factory=Impl2))

    with pytest.raises(CircularDependency, match=r"Svc.*Impl1.*Svc"):
        detect_cycles(reg)
