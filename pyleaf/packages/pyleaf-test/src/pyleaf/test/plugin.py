"""pytest plugin for pyleaf-backed tests (registered as the ``pyleaf`` EP).

Usage::

    @PyleafTest(scan="myapp", profiles=["test"], properties={"db.url": "mem:"})
    class TestService:
        def test_it(self, pyleaf_context):
            ...

The ``pyleaf_context`` fixture (function scope) builds an ``Environment``
(the ``@PyleafTest`` ``properties`` dict is layered last — over yaml/env —
via ``load_environment`` when ``config_dir`` is given), an
``ApplicationContext`` scanning ``scan``, applies bean overrides, refreshes,
yields the context and closes it on teardown. ``@PyleafTest`` metadata is
read from the test class first, then the function, then the module — like
pytest marks. A plain sync fixture is used so async tests simply await
inside them.

Bean overrides — ``mock_bean(T)`` / ``spy_bean(T)`` descriptors — are
declared by overriding the plugin's ``pyleaf_beans`` fixture::

    @pytest.fixture
    def pyleaf_beans():
        return [mock_bean(Repo)]

A ``pyleaf_beans`` *class attribute* holding a list of descriptors is
merged in as well. Overrides apply inside ``ctx.refresh()`` after
component scanning but before conditions and instantiation; attempting one
on an already-refreshed context raises ``PyleafError`` naming the bean.

The ``pyleaf_client`` fixture (function scope, depends on
``pyleaf_context``) serves the refreshed context over HTTP: it builds a
per-test FastAPI app with the same wiring ``fastapiApp`` produces —
controllers, advice, bean middleware, ``RequestScopeMiddleware`` — wraps
it in a ``TestClient`` and closes only the client on teardown; the
context remains ``pyleaf_context``'s to close. The web stack
(``fastapi`` / ``pyleaf.web``) is imported lazily; when it is absent the
test skips instead of failing at import.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import Mock

import pytest
from pyleaf.boot import load_environment
from pyleaf.core import (
    ApplicationContext,
    BeanDefinition,
    Environment,
    EnvVarSource,
    PropertySource,
    PyleafError,
)
from pyleaf.core.definition import Scope

if TYPE_CHECKING:
    from pyleaf.core.resolver import DepSpec
    from starlette.testclient import TestClient

_META_ATTR = "__pyleaf_test__"


def _type_name(tp: Any) -> str:
    name = getattr(tp, "__name__", None)
    return name if isinstance(name, str) else str(tp)


def _no_dep_specs() -> list[DepSpec]:
    return []


@dataclass(frozen=True)
class _PyleafTestMeta:
    """Configuration captured by ``@PyleafTest``."""

    scan: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()
    properties: Mapping[str, Any] | None = None
    config_dir: str | Path | None = None


_DEFAULT_META = _PyleafTestMeta()


def PyleafTest(
    scan: str | Sequence[str] = (),
    profiles: Sequence[str] = (),
    properties: Mapping[str, Any] | None = None,
    config_dir: str | Path | None = None,
) -> Any:
    """Mark a test class or function as pyleaf-backed.

    ``scan`` — package(s) to component-scan; ``profiles`` — active
    profiles; ``properties`` — a flat dict layered over every other
    property source (it wins); ``config_dir`` — directory holding
    ``application.yaml`` / ``application-<profile>.yaml`` loaded via
    :func:`pyleaf.boot.load_environment`. May also be used bare
    (``@PyleafTest``), equivalent to ``@PyleafTest()``.
    """
    if not isinstance(scan, (str, Sequence)):
        # Bare ``@PyleafTest``: the decorated node arrives as `scan`.
        node: Any = scan
        node.__pyleaf_test__ = _DEFAULT_META
        return node
    meta = _PyleafTestMeta(
        scan=(scan,) if isinstance(scan, str) else tuple(scan),
        profiles=tuple(profiles),
        properties=dict(properties) if properties is not None else None,
        config_dir=config_dir,
    )

    def decorator(node: Any) -> Any:
        node.__pyleaf_test__ = meta
        return node

    return decorator


@dataclass(frozen=True)
class BeanOverride:
    """A deferred bean replacement produced by :func:`mock_bean` /
    :func:`spy_bean`, applied to a context before it refreshes.

    ``mock`` replaces the target definition with a singleton
    ``Mock(spec=T)`` (registering one when no bean matches); ``spy`` wraps
    the original factory in ``Mock(wraps=<real instance>)`` — real
    behavior plus call recording — and errors when nothing matches.
    Overridden defs become ``internal``: injectable, but never advised,
    scanned for listeners, or force-instantiated as aspects /
    post-processors.
    """

    required: Any
    kind: Literal["mock", "spy"]

    def apply(self, ctx: ApplicationContext) -> None:
        """Queue this override on ``ctx``.

        Raises :class:`PyleafError` naming the bean when the context is
        already refreshed.
        """
        ctx.override_bean(self.required, self._transform)

    def _transform(self, old: BeanDefinition | None) -> BeanDefinition:
        target = _type_name(self.required)
        if self.kind == "spy":
            if old is None:
                raise PyleafError(
                    f"spy_bean: no bean of type '{target}' to spy on"
                )
            real_factory = old.factory
            return replace(
                old,
                factory=lambda: Mock(wraps=real_factory()),
                internal=True,
            )

        def factory() -> Any:
            return Mock(spec=self.required)

        if old is None:
            # Spring-style: mocking a bean that was never registered adds
            # one (primary, so it wins any later ambiguity).
            return BeanDefinition(
                name=target[:1].lower() + target[1:],
                bean_type=self.required,
                factory=factory,
                primary=True,
                internal=True,
            )
        return replace(
            old,
            factory=factory,
            scope=Scope.SINGLETON,
            dep_specs=_no_dep_specs,
            conditions=[],
            internal=True,
        )


def mock_bean(required: Any) -> BeanOverride:
    """Override the bean of type ``required`` with ``Mock(spec=required)``.

    ``ctx.get(required)`` — and every dependent's injection — sees the
    mock. When nothing matches, a new mock bean is registered instead.
    """
    return BeanOverride(required, "mock")


def spy_bean(required: Any) -> BeanOverride:
    """Override the bean of type ``required`` with
    ``Mock(wraps=<real instance>)``: real behavior plus call recording.
    Requires an existing bean of that type."""
    return BeanOverride(required, "spy")


def _meta_for(request: pytest.FixtureRequest) -> _PyleafTestMeta:
    """Resolve ``@PyleafTest`` meta: class first, then function, module."""
    for obj in (request.cls, request.function, request.module):
        if obj is None:
            continue
        meta = getattr(obj, _META_ATTR, None)
        if isinstance(meta, _PyleafTestMeta):
            return meta
    return _DEFAULT_META


def _build_environment(meta: _PyleafTestMeta) -> Environment:
    """Layered env for the test ctx; the properties dict is added last."""
    sources: list[PropertySource]
    if meta.config_dir is not None:
        loaded = load_environment(
            config_dir=meta.config_dir, profiles=meta.profiles
        )
        sources = loaded.sources
    else:
        sources = [EnvVarSource()]
    if meta.properties:
        sources.append(
            PropertySource(name="pyleaf-test", values=dict(meta.properties))
        )
    return Environment(sources=sources, active_profiles=meta.profiles)


@pytest.fixture
def pyleaf_beans() -> list[BeanOverride]:
    """Bean overrides applied to ``pyleaf_context`` before it refreshes.

    Override this fixture (module- or class-level) returning a list of
    ``mock_bean(...)`` / ``spy_bean(...)`` descriptors; a ``pyleaf_beans``
    class attribute holding a list is merged with this fixture's result.
    """
    return []


@pytest.fixture
def pyleaf_context(
    request: pytest.FixtureRequest, pyleaf_beans: list[BeanOverride]
) -> Iterator[ApplicationContext]:
    """Build, refresh, yield and close a per-test ``ApplicationContext``.

    Reads ``@PyleafTest`` meta (class > function > module), builds the
    environment, applies ``pyleaf_beans`` overrides pre-refresh, scans +
    refreshes, then closes on teardown.
    """
    meta = _meta_for(request)
    ctx = ApplicationContext(
        scan=list(meta.scan), environment=_build_environment(meta)
    )
    overrides = list(pyleaf_beans)
    cls = request.cls
    if cls is not None:
        # A plain `pyleaf_beans` class attribute of descriptors merges in;
        # a same-named fixture resolves through `pyleaf_beans` above.
        extra = getattr(cls, "pyleaf_beans", None)
        if isinstance(extra, (list, tuple)):
            overrides.extend(extra)
    for override in overrides:
        override.apply(ctx)
    ctx.refresh()
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def pyleaf_client(
    pyleaf_context: ApplicationContext,
) -> Iterator[TestClient]:
    """Serve ``pyleaf_context`` over a per-test ``TestClient``.

    Builds a fresh ``FastAPI`` app with the same wiring
    ``WebAutoConfiguration.fastapiApp`` produces — ``@RestController``
    routes, the ``@ControllerAdvice`` dispatcher, ``@WebMiddleware``
    beans, ``RequestScopeMiddleware``, CORS from ``pyleaf.web.cors.*``
    properties, ``/health`` and ``FastApiCustomizer`` beans — minus the
    lifespan: the context stays ``pyleaf_context``'s to close, so the
    fixture only closes the client. Building the app lazily at fixture
    time is what makes ``mock_bean``/``spy_bean`` overrides visible in
    served responses: controllers were already constructed against the
    overridden beans when the routes resolve them.

    ``fastapi`` / ``pyleaf.web`` are imported lazily so pyleaf-test does
    not hard-depend on the web stack; when it is absent the using test
    is skipped.
    """
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from pyleaf.web import (
            FastApiCustomizer,
            RequestScopeMiddleware,
            register_advice,
            register_controllers,
            register_middleware,
        )
        from pyleaf.web.autoconfigure import _add_cors
    except ImportError as exc:
        pytest.skip(
            "pyleaf_client requires the web stack — install pyleaf-web "
            f"(extra 'pyleaf-test[web]'); missing module: {exc.name!r}"
        )

    app = FastAPI()
    register_controllers(app, pyleaf_context)
    register_advice(app, pyleaf_context)
    register_middleware(app, pyleaf_context)
    app.add_middleware(RequestScopeMiddleware, context=pyleaf_context)
    _add_cors(app, pyleaf_context.environment)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "UP"}

    for customizer in pyleaf_context.get_all(FastApiCustomizer):
        customizer.customize(app)

    with TestClient(app) as client:
        yield client
