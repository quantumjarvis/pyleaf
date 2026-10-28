"""Unit tests pinning ConditionResult contents and conditions.py helpers.

test_conditions.py exercises the @ConditionalOn* family end-to-end through
refresh(); these tests assert the exact (matched, reason) pairs and the
helper functions directly so mutation testing verifies every string and
branch of conditions.py.
"""

import pytest
from pyleaf.core import (
    Conditional,
    ConditionalOnClass,
    ConditionContext,
    ConditionResult,
    Environment,
    PropertySource,
)
from pyleaf.core.conditions import (
    CONDITIONS_ATTR,
    OnBeanCondition,
    OnClassCondition,
    OnMissingBeanCondition,
    OnMissingClassCondition,
    OnProfileCondition,
    OnPropertyCondition,
    _type_name,
    conditions_of,
    evaluate_condition,
    is_bean_presence,
)
from pyleaf.core.definition import BeanDefinition, BeanRegistry


def _ctx(
    props: dict[str, str] | None = None,
    profiles: tuple[str, ...] = (),
    registry: BeanRegistry | None = None,
    importable: tuple[str, ...] = (),
    excluding: str | None = None,
) -> ConditionContext:
    return ConditionContext(
        environment=Environment(
            sources=[PropertySource("test", props or {})],
            active_profiles=profiles,
        ),
        registry=registry or BeanRegistry(),
        is_importable=lambda name: name in importable,
        excluding=excluding,
    )


# --- _type_name ---------------------------------------------------------------


class _Iface:
    pass


class _ImplA(_Iface):
    pass


class _ImplB(_Iface):
    pass


def test_type_name_of_class_is_name() -> None:
    assert _type_name(_Iface) == "_Iface"


def test_type_name_of_union_falls_back_to_str() -> None:
    assert _type_name(int | str) == "int | str"


def test_type_name_of_non_type_falls_back_to_str() -> None:
    assert _type_name(42) == "42"


# --- is_bean_presence ---------------------------------------------------------


def test_is_bean_presence_flags_bean_presence_conditions() -> None:
    assert is_bean_presence(OnBeanCondition(_Iface)) is True
    assert is_bean_presence(OnMissingBeanCondition(_Iface)) is True


def test_is_bean_presence_false_for_other_conditions_and_objects() -> None:
    assert is_bean_presence(OnClassCondition("x")) is False
    assert is_bean_presence(OnPropertyCondition("k")) is False
    assert is_bean_presence(object()) is False


# --- evaluate_condition -------------------------------------------------------


class _TrueCondition:
    def matches(self, ctx: ConditionContext) -> bool:
        return True


class _FalseCondition:
    def matches(self, ctx: ConditionContext) -> bool:
        return False


def test_evaluate_condition_wraps_true_bool_with_generic_reason() -> None:
    result = evaluate_condition(_TrueCondition(), _ctx())
    assert result.matched is True
    assert result.reason == "_TrueCondition matched"


def test_evaluate_condition_wraps_false_bool_with_generic_reason() -> None:
    result = evaluate_condition(_FalseCondition(), _ctx())
    assert result.matched is False
    assert result.reason == "_FalseCondition did not match"


def test_evaluate_condition_passes_condition_result_through() -> None:
    expected = ConditionResult(True, "custom reason")

    class _ResultCondition:
        def matches(self, ctx: ConditionContext) -> ConditionResult:
            return expected

    assert evaluate_condition(_ResultCondition(), _ctx()) is expected


# --- OnClassCondition -----------------------------------------------------------


def test_on_class_matches_via_parent_module() -> None:
    result = OnClassCondition("a.b.C").matches(_ctx(importable=("a.b",)))
    assert result.matched is True
    assert result.reason == "@ConditionalOnClass 'a.b.C' is present"


def test_on_class_matches_via_full_name_fallback() -> None:
    result = OnClassCondition("a.b.C").matches(_ctx(importable=("a.b.C",)))
    assert result.matched is True
    assert result.reason == "@ConditionalOnClass 'a.b.C' is present"


def test_on_class_no_match_when_nothing_importable() -> None:
    result = OnClassCondition("a.b.C").matches(_ctx())
    assert result.matched is False
    assert result.reason == "@ConditionalOnClass 'a.b.C' is not importable"


def test_on_class_bare_name_probes_full_name() -> None:
    result = OnClassCondition("json").matches(_ctx(importable=("json",)))
    assert result.matched is True
    assert result.reason == "@ConditionalOnClass 'json' is present"


# --- OnMissingClassCondition ----------------------------------------------------


def test_on_missing_class_matches_when_absent() -> None:
    result = OnMissingClassCondition("a.b.C").matches(_ctx())
    assert result.matched is True
    assert result.reason == "@ConditionalOnMissingClass 'a.b.C' is absent"


def test_on_missing_class_no_match_via_parent() -> None:
    result = OnMissingClassCondition("a.b.C").matches(_ctx(importable=("a.b",)))
    assert result.matched is False
    assert result.reason == "@ConditionalOnMissingClass 'a.b.C' is importable"


def test_on_missing_class_no_match_via_full_name() -> None:
    result = OnMissingClassCondition("a.b.C").matches(_ctx(importable=("a.b.C",)))
    assert result.matched is False
    assert result.reason == "@ConditionalOnMissingClass 'a.b.C' is importable"


# --- OnPropertyCondition --------------------------------------------------------


def test_on_property_constructor_defaults() -> None:
    cond = OnPropertyCondition("k")
    assert cond.having_value is None
    assert cond.match_if_missing is False


def test_on_property_matches_presence() -> None:
    result = OnPropertyCondition("app.mode").matches(
        _ctx(props={"app.mode": "dev"})
    )
    assert result.matched is True
    assert result.reason == (
        "@ConditionalOnProperty 'app.mode' matched (property is present)"
    )


def test_on_property_matches_having_value() -> None:
    cond = OnPropertyCondition("app.mode", having_value="prod")
    result = cond.matches(_ctx(props={"app.mode": "prod"}))
    assert result.matched is True
    assert result.reason == "@ConditionalOnProperty 'app.mode' = 'prod' matched"


def test_on_property_fails_on_wrong_value() -> None:
    cond = OnPropertyCondition("app.mode", having_value="prod")
    result = cond.matches(_ctx(props={"app.mode": "dev"}))
    assert result.matched is False
    assert result.reason == (
        "@ConditionalOnProperty 'app.mode' = 'prod' did not match 'dev'"
    )


def test_on_property_fails_when_missing_by_default() -> None:
    cond = OnPropertyCondition("app.mode", having_value="prod")
    result = cond.matches(_ctx())
    assert result.matched is False
    assert result.reason == (
        "@ConditionalOnProperty 'app.mode' = 'prod' did not match"
        " (property missing)"
    )


def test_on_property_matches_when_missing_and_match_if_missing() -> None:
    cond = OnPropertyCondition("app.mode", having_value="prod", match_if_missing=True)
    result = cond.matches(_ctx())
    assert result.matched is True
    assert result.reason == (
        "@ConditionalOnProperty 'app.mode' = 'prod' matched"
        " (property missing, match_if_missing=True)"
    )


# --- OnBeanCondition ------------------------------------------------------------


def _registry_with_impls() -> BeanRegistry:
    reg = BeanRegistry()
    reg.register(BeanDefinition("implA", _ImplA, _ImplA))
    reg.register(BeanDefinition("implB", _ImplB, _ImplB))
    return reg


def test_on_bean_matches_and_lists_candidates() -> None:
    result = OnBeanCondition(_Iface).matches(_ctx(registry=_registry_with_impls()))
    assert result.matched is True
    assert result.reason == "@ConditionalOnBean _Iface found beans: implA, implB"


def test_on_bean_no_match_when_registry_empty() -> None:
    result = OnBeanCondition(_Iface).matches(_ctx())
    assert result.matched is False
    assert result.reason == "@ConditionalOnBean _Iface found beans: none"


def test_on_bean_excludes_self_from_candidates() -> None:
    result = OnBeanCondition(_Iface).matches(
        _ctx(registry=_registry_with_impls(), excluding="implA")
    )
    assert result.matched is True
    assert result.reason == "@ConditionalOnBean _Iface found beans: implB"


def test_on_bean_excluding_only_candidate_is_no_match() -> None:
    reg = BeanRegistry()
    reg.register(BeanDefinition("implA", _ImplA, _ImplA))
    result = OnBeanCondition(_Iface).matches(_ctx(registry=reg, excluding="implA"))
    assert result.matched is False
    assert result.reason == "@ConditionalOnBean _Iface found beans: none"


# --- OnMissingBeanCondition -----------------------------------------------------


def test_on_missing_bean_matches_when_registry_empty() -> None:
    result = OnMissingBeanCondition(_Iface).matches(_ctx())
    assert result.matched is True
    assert result.reason == "@ConditionalOnMissingBean _Iface found beans: none"


def test_on_missing_bean_no_match_and_lists_candidates() -> None:
    result = OnMissingBeanCondition(_Iface).matches(
        _ctx(registry=_registry_with_impls())
    )
    assert result.matched is False
    assert result.reason == (
        "@ConditionalOnMissingBean _Iface found beans: implA, implB"
    )


def test_on_missing_bean_matches_when_only_candidate_is_self() -> None:
    reg = BeanRegistry()
    reg.register(BeanDefinition("implA", _ImplA, _ImplA))
    result = OnMissingBeanCondition(_Iface).matches(
        _ctx(registry=reg, excluding="implA")
    )
    assert result.matched is True
    assert result.reason == "@ConditionalOnMissingBean _Iface found beans: none"


# --- OnProfileCondition ---------------------------------------------------------


def test_on_profile_matches_active_profile() -> None:
    cond = OnProfileCondition(("dev", "prod"))
    result = cond.matches(_ctx(profiles=("dev",)))
    assert result.matched is True
    assert result.reason == (
        "@ConditionalOnProfile [dev, prod] matched (active: dev)"
    )


def test_on_profile_no_match_inactive() -> None:
    cond = OnProfileCondition(("dev", "prod"))
    result = cond.matches(_ctx(profiles=("staging",)))
    assert result.matched is False
    assert result.reason == (
        "@ConditionalOnProfile [dev, prod] did not match (active: staging)"
    )


def test_on_profile_reason_sorts_active_profiles() -> None:
    cond = OnProfileCondition(("dev",))
    result = cond.matches(_ctx(profiles=("zeta", "alpha")))
    assert result.matched is False
    assert result.reason == (
        "@ConditionalOnProfile [dev] did not match (active: alpha, zeta)"
    )


# --- Conditional / _add_condition / conditions_of --------------------------------


def test_conditions_of_undecorated_target_is_empty() -> None:
    class Plain:
        pass

    assert conditions_of(Plain) == []


def test_conditional_instantiates_condition_class() -> None:
    class Marker:
        def matches(self, ctx: ConditionContext) -> bool:
            return True

    @Conditional(Marker)
    class Bean:
        pass

    conds = conditions_of(Bean)
    assert len(conds) == 1
    assert isinstance(conds[0], Marker)


def test_conditional_uses_given_instance() -> None:
    class Marker:
        def matches(self, ctx: ConditionContext) -> bool:
            return True

    instance = Marker()

    @Conditional(instance)
    class Bean:
        pass

    assert conditions_of(Bean) == [instance]


def test_stacked_condition_decorators_append_in_order() -> None:
    @ConditionalOnClass("mod.a")
    @ConditionalOnClass("mod.b")
    class Bean:
        pass

    assert [c.name for c in conditions_of(Bean)] == ["mod.b", "mod.a"]


def test_subclass_condition_does_not_mutate_parent_list() -> None:
    """An inherited __pyleaf_conditions__ list is copied on first append so a
    condition on the subclass never leaks into the parent's list."""

    @ConditionalOnClass("mod.a")
    class Base:
        pass

    @ConditionalOnClass("mod.b")
    class Sub(Base):
        pass

    base_conds = conditions_of(Base)
    sub_conds = conditions_of(Sub)
    assert len(base_conds) == 1
    assert len(sub_conds) == 2
    assert sub_conds[0] is base_conds[0]
    assert getattr(Sub, CONDITIONS_ATTR) is not getattr(Base, CONDITIONS_ATTR)


def test_second_condition_appends_to_same_list_object() -> None:
    """A target whose own __dict__ already holds the marker list appends in
    place — the list is not re-copied."""

    @ConditionalOnClass("mod.a")
    class Bean:
        pass

    first = getattr(Bean, CONDITIONS_ATTR)
    ConditionalOnClass("mod.b")(Bean)
    assert getattr(Bean, CONDITIONS_ATTR) is first
    assert len(first) == 2


def test_condition_on_slotted_instance_fails_at_setattr() -> None:
    """A target with an inherited marker list but no __dict__ reaches the
    copy branch and fails at setattr — AttributeError, not a TypeError from
    the __dict__ lookup."""

    class SlotBase:
        __slots__ = ()

    ConditionalOnClass("mod.a")(SlotBase)
    with pytest.raises(AttributeError):
        ConditionalOnClass("mod.b")(SlotBase())
