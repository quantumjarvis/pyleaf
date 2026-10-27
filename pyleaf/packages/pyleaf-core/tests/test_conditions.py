"""Tests for conditional bean registration (phase-A conditions, AND semantics)."""

import pytest
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Component,
    Conditional,
    ConditionalOnBean,
    ConditionalOnClass,
    ConditionalOnMissingBean,
    ConditionalOnMissingClass,
    ConditionalOnProfile,
    ConditionalOnProperty,
    ConditionContext,
    Configuration,
    Environment,
    NoSuchBean,
    Profile,
    PropertySource,
)


def _ctx(*classes: type, env: Environment | None = None) -> ApplicationContext:
    ctx = (
        ApplicationContext(environment=env)
        if env is not None
        else ApplicationContext()
    )
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def _env_with(props: dict[str, str]) -> Environment:
    return Environment(sources=[PropertySource("test", props)])


# --- @ConditionalOnClass -----------------------------------------------------


def test_conditional_on_class_matches_importable_module() -> None:
    @Component
    class AlwaysBean:
        pass

    @Component
    @ConditionalOnClass("json")
    class ConditionalBean:
        pass

    ctx = _ctx(AlwaysBean, ConditionalBean)
    assert isinstance(ctx.get_bean("alwaysBean"), AlwaysBean)
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


def test_conditional_on_class_skips_missing_module() -> None:
    @Component
    class AlwaysBean:
        pass

    @Component
    @ConditionalOnClass("nonexistent_module_xyz")
    class ConditionalBean:
        pass

    ctx = _ctx(AlwaysBean, ConditionalBean)
    assert isinstance(ctx.get_bean("alwaysBean"), AlwaysBean)
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


def test_conditional_on_class_does_not_execute_module() -> None:
    """find_spec proves importability without running the module.

    fixture_cond.mod_noexec raises RuntimeError on import. The condition must
    match (the module IS importable) and refresh must not raise.
    """

    @Component
    @ConditionalOnClass("fixture_cond.mod_noexec")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean)
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


# --- @ConditionalOnMissingClass ----------------------------------------------


def test_conditional_on_missing_class_registers_when_absent() -> None:
    @Component
    @ConditionalOnMissingClass("nonexistent_module_xyz")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean)
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


def test_conditional_on_missing_class_skips_when_present() -> None:
    @Component
    @ConditionalOnMissingClass("json")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean)
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


# --- @ConditionalOnProperty ---------------------------------------------------


def test_conditional_on_property_matches_having_value() -> None:
    @Component
    @ConditionalOnProperty("app.mode", having_value="prod")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=_env_with({"app.mode": "prod"}))
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


def test_conditional_on_property_fails_on_wrong_value() -> None:
    @Component
    @ConditionalOnProperty("app.mode", having_value="prod")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=_env_with({"app.mode": "dev"}))
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


def test_conditional_on_property_fails_when_missing() -> None:
    @Component
    @ConditionalOnProperty("app.mode", having_value="prod")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=_env_with({}))
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


def test_conditional_on_property_match_if_missing_registers() -> None:
    @Component
    @ConditionalOnProperty("app.mode", having_value="prod", match_if_missing=True)
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=_env_with({}))
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


# --- @ConditionalOnBean -------------------------------------------------------


def test_conditional_on_bean_registers_when_dependency_present() -> None:
    @Component
    class Dep:
        pass

    @Component
    @ConditionalOnBean(Dep)
    class ConditionalBean:
        pass

    ctx = _ctx(Dep, ConditionalBean)
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


def test_conditional_on_bean_skips_when_dependency_absent() -> None:
    class Dep:
        pass

    @Component
    @ConditionalOnBean(Dep)
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean)
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


# --- @ConditionalOnMissingBean ------------------------------------------------


def test_conditional_on_missing_bean_registers_when_no_impl() -> None:
    class Iface:
        pass

    @Component
    @ConditionalOnMissingBean(Iface)
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean)
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


def test_conditional_on_missing_bean_skips_when_impl_registered() -> None:
    class Iface:
        pass

    @Component
    class IfaceImpl(Iface):
        pass

    @Component
    @ConditionalOnMissingBean(Iface)
    class ConditionalBean:
        pass

    ctx = _ctx(IfaceImpl, ConditionalBean)
    assert isinstance(ctx.get_bean("ifaceImpl"), IfaceImpl)
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


# --- @ConditionalOnProfile / @Profile -----------------------------------------


def test_conditional_on_profile_matches_active_profile() -> None:
    @Component
    @ConditionalOnProfile("dev")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=Environment(active_profiles=["dev"]))
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


def test_conditional_on_profile_skips_inactive_profile() -> None:
    @Component
    @ConditionalOnProfile("dev")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=Environment(active_profiles=["prod"]))
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


def test_profile_sugar_matches_active_profile() -> None:
    @Component
    @Profile("dev")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=Environment(active_profiles=["dev"]))
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


def test_profile_sugar_skips_inactive_profile() -> None:
    @Component
    @Profile("dev")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=Environment(active_profiles=["prod"]))
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


# --- @Conditional (custom) ----------------------------------------------------


class AlwaysCondition:
    def matches(self, ctx: ConditionContext) -> bool:
        return True


class NeverCondition:
    def matches(self, ctx: ConditionContext) -> bool:
        return False


def test_conditional_custom_condition_match_registers() -> None:
    @Component
    @Conditional(AlwaysCondition)
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean)
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


def test_conditional_custom_condition_no_match_skips() -> None:
    @Component
    @Conditional(NeverCondition)
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean)
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


# --- Multiple conditions: AND semantics ----------------------------------------


def test_multiple_conditions_register_when_all_match() -> None:
    @Component
    @ConditionalOnClass("json")
    @ConditionalOnProperty("x", having_value="1")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=_env_with({"x": "1"}))
    assert isinstance(ctx.get_bean("conditionalBean"), ConditionalBean)


def test_multiple_conditions_skip_when_property_fails() -> None:
    @Component
    @ConditionalOnClass("json")
    @ConditionalOnProperty("x", having_value="1")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=_env_with({"x": "0"}))
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


def test_multiple_conditions_skip_when_class_fails() -> None:
    @Component
    @ConditionalOnClass("nonexistent_module_xyz")
    @ConditionalOnProperty("x", having_value="1")
    class ConditionalBean:
        pass

    ctx = _ctx(ConditionalBean, env=_env_with({"x": "1"}))
    with pytest.raises(NoSuchBean):
        ctx.get_bean("conditionalBean")


# --- Conditions on @Bean methods ------------------------------------------------


def test_condition_on_bean_method_gates_only_that_bean() -> None:
    class Gated:
        pass

    class Included:
        pass

    class Plain:
        pass

    @Configuration
    class MethodConfig:
        @Bean
        @ConditionalOnClass("nonexistent_module_xyz")
        def gated(self) -> Gated:
            return Gated()

        @Bean
        @ConditionalOnClass("json")
        def included(self) -> Included:
            return Included()

        @Bean
        def plain(self) -> Plain:
            return Plain()

    ctx = _ctx(MethodConfig)
    assert isinstance(ctx.get_bean("methodConfig"), MethodConfig)
    assert isinstance(ctx.get_bean("plain"), Plain)
    assert isinstance(ctx.get_bean("included"), Included)
    with pytest.raises(NoSuchBean):
        ctx.get_bean("gated")
