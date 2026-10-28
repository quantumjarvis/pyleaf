"""Edge-case coverage for the payload builders in ``endpoints.py``.

The contract tests exercise the happy paths; these cover the branches they
miss: a ``health()`` that raises, non-class bean types / required types in
``_candidates``, internal-definition skipping, qualifier / list / primary
dependency resolution, a ``dep_specs`` that blows up, and the
``_prometheus_available`` fallback when ``find_spec`` itself raises.
"""

import importlib.util
from typing import Annotated, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyleaf.actuator import ActuatorAutoConfiguration
from pyleaf.actuator.endpoints import beans_payload, health_payload
from pyleaf.core import (
    ApplicationContext,
    BeanDefinition,
    Component,
    Environment,
    EventPublisher,
    Primary,
    PropertySource,
    Qualifier,
)
from pyleaf.web.autoconfigure import WebAutoConfiguration


def _ctx(
    *classes: type, values: dict[str, object] | None = None
) -> ApplicationContext:
    ctx = ApplicationContext(
        environment=Environment(sources=[PropertySource("test", values or {})])
    )
    for cls in classes:
        ctx.register(cls)
    ctx.refresh()
    return ctx


def _beans(ctx: ApplicationContext) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in beans_payload(ctx)}


def test_health_payload_marks_failing_indicator_down() -> None:
    """A ``health()`` that raises reports DOWN with an error detail."""

    @Component
    class Flaky:
        def health(self) -> dict[str, str]:
            raise RuntimeError("probe exploded")

    ctx = _ctx(Flaky)
    status, body = health_payload(ctx)

    assert status == 503
    assert body["status"] == "DOWN"
    assert body["components"]["flaky"] == {
        "status": "DOWN",
        "error": "health() raised",
    }


def test_beans_resolves_qualifier_dependency() -> None:
    """``Annotated[T, Qualifier("name")]`` narrows candidates to that bean."""

    class Base:
        pass

    @Component
    class ImplA(Base):
        pass

    @Component
    class ImplB(Base):
        pass

    @Component
    class Qualified:
        def __init__(self, dep: Annotated[Base, Qualifier("implB")]) -> None:
            self.dep = dep

    ctx = _ctx(ImplA, ImplB, Qualified)

    assert _beans(ctx)["qualified"]["dependencies"] == ["implB"]


def test_beans_resolves_list_dependency_to_all_candidates() -> None:
    """``list[T]`` deps resolve to every candidate name."""

    class Base:
        pass

    @Component
    class ImplA(Base):
        pass

    @Component
    class ImplB(Base):
        pass

    @Component
    class ListConsumer:
        def __init__(self, deps: list[Base]) -> None:
            self.deps = deps

    ctx = _ctx(ImplA, ImplB, ListConsumer)

    assert sorted(_beans(ctx)["listConsumer"]["dependencies"]) == [
        "implA",
        "implB",
    ]


def test_beans_resolves_ambiguous_dependency_to_primary() -> None:
    """Several candidates + exactly one @Primary -> the primary name."""

    class Base:
        pass

    @Component
    class ImplA(Base):
        pass

    @Component
    @Primary
    class ImplB(Base):
        pass

    @Component
    class Ambiguous:
        def __init__(self, dep: Base) -> None:
            self.dep = dep

    ctx = _ctx(ImplA, ImplB, Ambiguous)

    assert _beans(ctx)["ambiguous"]["dependencies"] == ["implB"]


def test_beans_event_publisher_dep_skips_non_exact_internal_defs() -> None:
    """Internal defs resolve by their exact declared type only.

    ``pub: EventPublisher`` makes every protocol-compatible bean a subclass
    candidate — ``issubclass(ApplicationContext, EventPublisher)`` is True —
    but the internal ``applicationContext`` def only matches its exact type,
    so the dep resolves to ``eventPublisher`` alone.
    """

    @Component
    class WantsPub:
        def __init__(self, pub: EventPublisher) -> None:
            self.pub = pub

    ctx = _ctx(WantsPub)

    assert _beans(ctx)["wantsPub"]["dependencies"] == ["eventPublisher"]


def test_beans_tolerates_non_class_types() -> None:
    """Generic-alias required types and non-class bean types don't crash."""

    @Component
    class NeedsMapping:
        def __init__(self, cfg: dict[str, int]) -> None:
            self.cfg = cfg

    ctx = _ctx(NeedsMapping)
    # bean_type that is not a class: ``issubclass`` hits the TypeError arm in
    # _candidates and _qualified_name falls back to str().
    union_type = int | str
    ctx._registry.register(
        BeanDefinition(name="odd", bean_type=union_type, factory=dict)
    )

    entries = _beans(ctx)
    # dict[str, int] as the required type: every def hits the TypeError arm,
    # zero candidates survive -> empty dependency list.
    assert entries["needsMapping"]["dependencies"] == []
    assert entries["odd"]["type"] == str(union_type)


def test_beans_ignores_definition_whose_dep_specs_fail() -> None:
    """A def whose ``dep_specs()`` raises reports no dependencies."""

    class Fragile:
        pass

    def _exploding_specs() -> list[Any]:
        raise RuntimeError("unresolvable hints")

    ctx = _ctx()
    ctx._registry.register(
        BeanDefinition(
            name="fragile",
            bean_type=Fragile,
            factory=Fragile,
            dep_specs=_exploding_specs,
        )
    )

    assert _beans(ctx)["fragile"]["dependencies"] == []


def test_metrics_route_absent_when_find_spec_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``find_spec`` raising -> _prometheus_available() False -> no route."""
    # fastapiApp is created at refresh(), so the patch must precede _ctx();
    # it is selective because @ConditionalOnClass calls find_spec too.
    real_find_spec = importlib.util.find_spec

    def _boom(name: str, *args: object, **kwargs: object) -> Any:
        if name == "prometheus_client":
            raise RuntimeError("finder exploded")
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    ctx = _ctx(WebAutoConfiguration, ActuatorAutoConfiguration)
    client = TestClient(ctx.get(FastAPI))

    assert client.get("/actuator/metrics").status_code == 404
    # The other endpoints are unaffected.
    assert client.get("/actuator/health").status_code == 200
