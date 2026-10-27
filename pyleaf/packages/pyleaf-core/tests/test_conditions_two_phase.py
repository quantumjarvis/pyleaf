"""Two-phase condition evaluation.

Bean-presence conditions (@ConditionalOnBean / @ConditionalOnMissingBean) run
in phase B, after phase A has dropped defs failing class/property/profile
conditions — so registration order must not matter and phase-A-dropped defs
must not count as present.
"""

import pytest
from pyleaf.core import (
    ApplicationContext,
    Bean,
    Component,
    ConditionalOnClass,
    ConditionalOnMissingBean,
    Configuration,
    NoSuchBean,
)


def test_user_bean_wins_on_missing_bean_registered_after_config() -> None:
    """User bean registered AFTER the config but BEFORE refresh still wins."""

    class Cache:
        pass

    @Configuration
    class CacheConfig:
        @Bean
        @ConditionalOnMissingBean(Cache)
        def default_cache(self) -> Cache:
            return Cache()

    @Component
    class UserCache(Cache):
        pass

    ctx = ApplicationContext()
    ctx.register(CacheConfig)
    ctx.register(UserCache)
    ctx.refresh()

    with pytest.raises(NoSuchBean):
        ctx.get_bean("default_cache")
    assert type(ctx.get(Cache)) is UserCache


def test_user_bean_wins_on_missing_bean_registered_before_config() -> None:
    """Registration order must not matter for the same outcome."""

    class Cache:
        pass

    @Component
    class UserCache(Cache):
        pass

    @Configuration
    class CacheConfig:
        @Bean
        @ConditionalOnMissingBean(Cache)
        def default_cache(self) -> Cache:
            return Cache()

    ctx = ApplicationContext()
    ctx.register(UserCache)
    ctx.register(CacheConfig)
    ctx.refresh()

    with pytest.raises(NoSuchBean):
        ctx.get_bean("default_cache")
    assert type(ctx.get(Cache)) is UserCache


def test_conditional_bean_registers_when_user_provides_none() -> None:
    class Cache:
        pass

    @Configuration
    class CacheConfig:
        @Bean
        @ConditionalOnMissingBean(Cache)
        def default_cache(self) -> Cache:
            return Cache()

    ctx = ApplicationContext()
    ctx.register(CacheConfig)
    ctx.refresh()

    assert type(ctx.get_bean("default_cache")) is Cache


def test_phase_a_dropped_def_does_not_count_for_on_missing_bean() -> None:
    """A def dropped in phase A must not satisfy a later OnMissingBean."""

    class Store:
        pass

    @Component
    @ConditionalOnClass("nonexistent_module_xyz")
    class PhantomStore(Store):
        pass

    @Configuration
    class StoreConfig:
        @Bean
        @ConditionalOnMissingBean(Store)
        def default_store(self) -> Store:
            return Store()

    ctx = ApplicationContext()
    ctx.register(PhantomStore)
    ctx.register(StoreConfig)
    ctx.refresh()

    with pytest.raises(NoSuchBean):
        ctx.get_bean("phantomStore")
    assert type(ctx.get_bean("default_store")) is Store
