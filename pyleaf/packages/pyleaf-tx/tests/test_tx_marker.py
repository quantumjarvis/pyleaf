"""@Transactional marker: forms, class-vs-method precedence, manager
resolution, error paths, and the TxAutoConfiguration wiring."""

from __future__ import annotations

import importlib.metadata
from typing import Any

import pytest
from pyleaf.core import (
    ApplicationContext,
    Component,
    Environment,
    PropertySource,
)
from pyleaf.tx import (
    Propagation,
    Transactional,
    TransactionError,
    TxAutoConfiguration,
    transactional_meta,
)
from pyleaf.tx import (
    TransactionSynchronizationManager as TSM,
)
from tx_fakes import (
    AsyncOnlyManager,
    RecordingManager,
    RecordingManagerBean,
    SyncOnlyManager,
)


@pytest.fixture(autouse=True)
def clean_context() -> Any:
    TSM.clear()
    yield
    TSM.clear()


def _ctx(*classes: type, env: Environment | None = None) -> ApplicationContext:
    ctx = ApplicationContext(environment=env)
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


# --- marker forms --------------------------------------------------------------


def test_bare_and_kwargs_forms_store_equal_definitions() -> None:
    @Transactional
    def bare() -> None: ...

    @Transactional()
    def parens() -> None: ...

    @Transactional(propagation=Propagation.SUPPORTS, read_only=True, timeout=2.5)
    def tuned() -> None: ...

    bare_meta = transactional_meta(object, bare)
    parens_meta = transactional_meta(object, parens)
    tuned_meta = transactional_meta(object, tuned)
    assert bare_meta is not None and parens_meta is not None
    assert bare_meta.definition == parens_meta.definition
    assert tuned_meta is not None
    assert tuned_meta.definition.propagation is Propagation.SUPPORTS
    assert tuned_meta.definition.read_only is True
    assert tuned_meta.definition.timeout_s == 2.5


# --- class-vs-method precedence -------------------------------------------------


@Component
@Transactional
class ClassLevelService:
    def __init__(self, manager: RecordingManagerBean) -> None:
        self.mgr: RecordingManager = manager

    def plain_work(self) -> str:
        return "class-default"

    @Transactional(propagation=Propagation.REQUIRES_NEW)
    def fresh_work(self) -> str:
        return "method-override"


def test_class_level_marker_covers_public_methods() -> None:
    ctx = _ctx(RecordingManagerBean, ClassLevelService, TxAutoConfiguration)
    svc = ctx.get(ClassLevelService)
    assert svc.plain_work() == "class-default"
    assert svc.mgr.calls == [("begin", "REQUIRED"), ("commit", "tx1")]


def test_method_level_overrides_class_level() -> None:
    ctx = _ctx(RecordingManagerBean, ClassLevelService, TxAutoConfiguration)
    svc = ctx.get(ClassLevelService)
    # plain_work is the class default; fresh_work overrides it entirely.
    svc.plain_work()
    assert svc.fresh_work() == "method-override"
    calls = svc.mgr.calls
    assert ("begin", "REQUIRES_NEW") in calls
    assert ("commit", "tx2") in calls and ("commit", "tx1") in calls


def test_transactional_meta_lookup_order() -> None:
    @Transactional(propagation=Propagation.SUPPORTS)
    class Kls:
        @Transactional(propagation=Propagation.NEVER)
        def overridden(self) -> None: ...

        def inherited(self) -> None: ...

    overridden = transactional_meta(Kls, Kls.overridden)
    inherited = transactional_meta(Kls, Kls.inherited)
    plain = transactional_meta(Kls, lambda: None)
    assert overridden is not None
    assert overridden.definition.propagation is Propagation.NEVER
    assert inherited is not None
    assert inherited.definition.propagation is Propagation.SUPPORTS
    assert plain is not None  # class-level applies to unmarked methods


# --- manager resolution ------------------------------------------------------------


@Component
class NamedManagerService:
    def __init__(self) -> None:
        self.done = False

    @Transactional(manager="secondManager")
    def work(self) -> str:
        return "named"


@Component(name="secondManager")
class SecondManager(RecordingManager):
    def __init__(self) -> None:
        super().__init__()


def test_named_manager_resolves_by_bean_name() -> None:
    ctx = _ctx(
        RecordingManagerBean, SecondManager, NamedManagerService,
        TxAutoConfiguration,
    )
    svc = ctx.get(NamedManagerService)
    assert svc.work() == "named"
    second = ctx.get(SecondManager)
    primary = ctx.get(RecordingManagerBean)
    # Only the named manager saw calls; by-type would be ambiguous here.
    assert second.calls == [("begin", "REQUIRED"), ("commit", "tx1")]
    assert primary.calls == []


# --- error paths (pinned: first-invocation, cached) ---------------------------------


@Component
class SyncWorker:
    @Transactional
    def work(self) -> str:
        return "x"


@Component
class AsyncWorker:
    @Transactional
    async def work(self) -> str:
        return "x"


def test_sync_method_with_only_async_manager_fails_clearly() -> None:
    ctx = _ctx(AsyncOnlyManager, SyncWorker, TxAutoConfiguration)
    svc = ctx.get(SyncWorker)
    with pytest.raises(TransactionError, match=r"SyncWorker\.work.*async-only"):
        svc.work()
    assert not TSM.is_active()


async def test_async_method_with_only_sync_manager_fails_clearly() -> None:
    ctx = _ctx(SyncOnlyManager, AsyncWorker, TxAutoConfiguration)
    svc = ctx.get(AsyncWorker)
    with pytest.raises(TransactionError, match=r"AsyncWorker\.work"):
        await svc.work()
    assert not TSM.is_active()


def test_no_manager_bean_fails_at_first_invocation() -> None:
    ctx = _ctx(SyncWorker, TxAutoConfiguration)
    svc = ctx.get(SyncWorker)
    with pytest.raises(
        TransactionError, match=r"SyncWorker\.work.*TransactionManager"
    ):
        svc.work()


async def test_no_manager_bean_fails_at_first_invocation_async() -> None:
    ctx = _ctx(AsyncWorker, TxAutoConfiguration)
    svc = ctx.get(AsyncWorker)
    with pytest.raises(TransactionError, match="AsyncTransactionManager"):
        await svc.work()


def test_named_manager_missing_fails_clearly() -> None:
    @Component
    class NamedMissing:
        @Transactional(manager="nope")
        def work(self) -> str:
            return "x"

    ctx = _ctx(RecordingManagerBean, NamedMissing, TxAutoConfiguration)
    svc = ctx.get(NamedMissing)
    with pytest.raises(TransactionError, match="'nope'"):
        svc.work()


# --- generator rejection ----------------------------------------------------------


def test_transactional_generator_fails_at_refresh() -> None:
    @Component
    class GenService:
        @Transactional
        def stream(self) -> Any:
            yield 1

    ctx = ApplicationContext()
    ctx.register(RecordingManagerBean)
    ctx.register(GenService)
    ctx.register(TxAutoConfiguration)
    with pytest.raises(TypeError, match=r"GenService\.stream"):
        ctx.refresh()


def test_transactional_async_generator_fails_at_refresh() -> None:
    @Component
    class AsyncGenService:
        @Transactional
        async def stream(self) -> Any:
            yield 1

    ctx = ApplicationContext()
    ctx.register(RecordingManagerBean)
    ctx.register(AsyncGenService)
    ctx.register(TxAutoConfiguration)
    with pytest.raises(TypeError, match=r"AsyncGenService\.stream"):
        ctx.refresh()


# --- autoconfigure ------------------------------------------------------------------


def test_autoconfigure_provides_the_interceptor_bean() -> None:
    from pyleaf.tx.interceptor import TransactionInterceptor

    ctx = _ctx(RecordingManagerBean, SyncWorker, TxAutoConfiguration)
    aspect = ctx.get_bean("transactionInterceptor")
    assert isinstance(aspect, TransactionInterceptor)


def test_order_env_property_overrides_default() -> None:
    from pyleaf.tx.interceptor import TransactionInterceptor

    env = Environment(
        sources=[PropertySource("test", {"pyleaf.tx.order": "5"})]
    )
    try:
        _ctx(RecordingManagerBean, SyncWorker, TxAutoConfiguration, env=env)
        assert TransactionInterceptor.__pyleaf_order__ == 5
    finally:
        # The property mutates the aspect class — restore the default.
        TransactionInterceptor.__pyleaf_order__ = 100


def test_entry_point_loads_tx_autoconfigure() -> None:
    """The ``pyleaf.autoconfigure`` entry point resolves to the class."""
    found = {
        ep.name: ep.load()
        for ep in importlib.metadata.entry_points(group="pyleaf.autoconfigure")
        if ep.name == "tx"
    }
    assert found.get("tx") is TxAutoConfiguration
