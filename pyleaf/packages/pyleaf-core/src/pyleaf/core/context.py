"""The pyleaf application context: registration, resolution, lifecycle."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, overload

from .aop import (
    ADVICE_ATTR,
    AdviceSpec,
    Invocation,
    Pointcut,
    advice_order,
    compile_link,
    is_aspect,
    iter_advices,
    iter_candidate_methods,
    make_wrapper,
)
from .conditions import (
    ConditionContext,
    ConditionEvaluationReport,
    conditions_of,
    evaluate_condition,
    is_bean_presence,
)
from .definition import DEFAULT_ORDER, BeanDefinition, BeanRegistry, Scope
from .environment import Environment, EnvVarSource
from .errors import (
    BeanCreationError,
    CircularDependency,
    NoSuchBean,
    NoUniqueBean,
    PyleafError,
)
from .events import (
    EventPublisher,
    ListenerEntry,
    event_matches,
    iter_event_listeners,
)
from .lifecycle import POST_CONSTRUCT_ATTR, PRE_DESTROY_ATTR, lifecycle_methods
from .postprocess import AFTER_INIT_HOOK, BEFORE_INIT_HOOK, is_bean_post_processor
from .resolver import (
    DepKind,
    DepSpec,
    bean_return_type,
    callable_dependencies,
    constructor_dependencies,
    detect_cycles,
    peel_annotated,
)
from .scan import scan_packages
from .stereotypes import (
    BEAN_ATTR,
    COMPONENT_ATTR,
    CONFIG_PROPS_ATTR,
    CONFIGURATION_ATTR,
    LAZY_ATTR,
    ORDER_ATTR,
    PRIMARY_ATTR,
    BeanMeta,
    ComponentMeta,
    ConfigurationMeta,
    decapitalize,
)

T = TypeVar("T")

_logger = logging.getLogger(__name__)


def _type_name(tp: Any) -> str:
    name = getattr(tp, "__name__", None)
    return name if isinstance(name, str) else str(tp)


@dataclass(frozen=True)
class _AdviceBinding:
    """One advice method matched against bean methods, ordered and compiled
    into a chain link at wrap time."""

    aspect_name: str
    aspect_type: type[Any]
    advice_method: str
    func: Callable[..., Any]
    kind: str
    order: int
    pointcut: Pointcut


def _make_is_importable() -> Callable[[str], bool]:
    """Build an ``is_importable`` predicate cached for one refresh pass.

    ``find_spec`` locates a module without executing it — a bean conditioned
    on a module that would raise on import still registers correctly. Odd
    names that make ``find_spec`` itself raise count as not importable.
    """
    cache: dict[str, bool] = {}

    def is_importable(name: str) -> bool:
        if name not in cache:
            try:
                cache[name] = importlib.util.find_spec(name) is not None
            except Exception:
                cache[name] = False
        return cache[name]

    return is_importable


def _iter_bean_methods(cls: type[Any]) -> Iterator[tuple[str, Callable[..., Any]]]:
    """Yield ``(method_name, function)`` for each ``@Bean``-marked method."""
    seen: set[str] = set()
    for klass in cls.__mro__:
        for attr_name, member in vars(klass).items():
            if attr_name in seen:
                continue
            func = member
            if isinstance(member, (staticmethod, classmethod)):
                func = member.__func__
            has_marker = getattr(member, BEAN_ATTR, None) is not None or (
                callable(func) and getattr(func, BEAN_ATTR, None) is not None
            )
            if callable(func) and has_marker:
                seen.add(attr_name)
                yield attr_name, func


class ApplicationContext:
    """Dependency-injection container."""

    def __init__(
        self,
        scan: Sequence[str] | None = None,
        exclude: Sequence[str] = (),
        environment: Environment | None = None,
    ) -> None:
        self._registry = BeanRegistry()
        self._singletons: dict[str, Any] = {}
        self._creating: list[str] = []
        self._resolved: dict[Any, BeanDefinition] = {}
        self._scan = list(scan) if scan is not None else []
        self._exclude = list(exclude)
        self._post_processors: list[Any] = []
        self._created: list[Any] = []
        self._aspect_bindings: list[_AdviceBinding] | None = None
        self._aop_matches: dict[
            str, dict[str, tuple[Callable[..., Any], list[_AdviceBinding]]]
        ] = {}
        self._environment = (
            environment
            if environment is not None
            else Environment(sources=[EnvVarSource()])
        )
        self._condition_report = ConditionEvaluationReport()
        self._listeners: list[ListenerEntry] = []
        self._listeners_dirty = True
        # Expose the environment itself as an injectable, pre-built bean.
        self._registry.register(
            BeanDefinition(
                name="environment",
                bean_type=Environment,
                factory=lambda: self._environment,
                internal=True,
            )
        )
        # Expose the context as the injectable event publisher.
        self._registry.register(
            BeanDefinition(
                name="eventPublisher",
                bean_type=EventPublisher,
                factory=lambda: self,
                internal=True,
            )
        )

    def register(self, cls: type[T]) -> type[T]:
        """Register a component-stereotype, ``@Configuration`` or
        ``@ConfigurationProperties`` (pyleaf-boot) class."""
        # New definitions may change the outcome of earlier resolutions
        # (extra candidate, new @Primary, qualifier match), so cached
        # definition lookups are stale.
        self._resolved.clear()
        # A newly registered aspect changes the match set of every existing
        # bean; recomputed lazily per definition on next creation.
        self._aspect_bindings = None
        self._aop_matches.clear()
        # A newly registered bean may declare event listeners.
        self._listeners_dirty = True
        if getattr(cls, COMPONENT_ATTR, None) is not None:
            self._register_component(cls)
        elif getattr(cls, CONFIGURATION_ATTR, None) is not None:
            self._register_configuration(cls)
        elif getattr(cls, CONFIG_PROPS_ATTR, None) is not None:
            self._register_config_properties(cls)
        else:
            raise PyleafError(
                f"{getattr(cls, '__name__', cls)} is neither a component"
                " nor a configuration class"
            )
        return cls

    def _register_component(self, cls: type[Any]) -> None:
        meta: ComponentMeta = getattr(cls, COMPONENT_ATTR)
        name = meta.name or decapitalize(cls.__name__)
        lazy = getattr(cls, LAZY_ATTR, meta.lazy)
        defn = BeanDefinition(
            name=name,
            bean_type=cls,
            factory=lambda: self._construct(cls),
            scope=meta.scope,
            primary=bool(getattr(cls, PRIMARY_ATTR, False)),
            lazy=bool(lazy),
            order=int(getattr(cls, ORDER_ATTR, DEFAULT_ORDER)),
            dep_specs=lambda: constructor_dependencies(cls, self._registry),
            conditions=conditions_of(cls),
        )
        self._registry.register(defn)

    def _register_configuration(self, cls: type[Any]) -> None:
        meta: ConfigurationMeta = getattr(cls, CONFIGURATION_ATTR)
        config_name = meta.name or decapitalize(cls.__name__)
        config_defn = BeanDefinition(
            name=config_name,
            bean_type=cls,
            factory=lambda: self._construct(cls),
            dep_specs=lambda: constructor_dependencies(cls, self._registry),
            conditions=conditions_of(cls),
        )
        self._registry.register(config_defn)
        for method_name, func in _iter_bean_methods(cls):
            self._register_bean_method(cls, config_name, method_name, func)

    def _register_config_properties(self, cls: type[Any]) -> None:
        """Register a ``@ConfigurationProperties``-marked class (pyleaf-boot).

        Extension point: the marker object attached by boot's decorator
        builds the :class:`BeanDefinition` itself via
        ``meta.build_definition(ctx, cls)`` — property binding (pydantic)
        lives in boot while registration dispatch stays in core. The
        resulting def is an ordinary singleton: conditions, AOP and bean
        post-processors all apply.
        """
        meta = getattr(cls, CONFIG_PROPS_ATTR)
        self._registry.register(meta.build_definition(self, cls))

    def _register_bean_method(
        self,
        config_cls: type[Any],
        config_name: str,
        method_name: str,
        func: Callable[..., Any],
    ) -> None:
        bean_meta: BeanMeta = getattr(func, BEAN_ATTR)
        owner = f"{config_cls.__name__}.{method_name}"
        bean_type = bean_return_type(func, owner, self._registry)
        bean_name = bean_meta.name or method_name

        def dep_specs() -> list[DepSpec]:
            specs = callable_dependencies(func, owner, self._registry)
            # Creating the bean requires the configuration instance.
            specs.append(DepSpec("__configuration__", DepKind.SINGLE, config_cls))
            return specs

        defn = BeanDefinition(
            name=bean_name,
            bean_type=bean_type,
            factory=lambda: self._invoke_bean_factory(
                config_name, method_name, func, owner
            ),
            scope=Scope.SINGLETON,
            lazy=False,
            dep_specs=dep_specs,
            conditions=conditions_of(func),
        )
        self._registry.register(defn)

    def _invoke_bean_factory(
        self,
        config_name: str,
        method_name: str,
        func: Callable[..., Any],
        owner: str,
    ) -> Any:
        config = self._get_or_create(self._registry.get(config_name))
        specs = callable_dependencies(func, owner, self._registry)
        kwargs = {spec.name: self._inject(spec) for spec in specs}
        return getattr(config, method_name)(**kwargs)

    def _construct(self, cls: type[T]) -> T:
        specs = constructor_dependencies(cls, self._registry)
        kwargs = {spec.name: self._inject(spec) for spec in specs}
        return cls(**kwargs)

    def refresh(self, eager: bool = False) -> None:
        """Scan packages, instantiate post-processors, validate the graph,
        then pre-instantiate singletons.

        Scanning registers new definitions, so cycle detection runs after the
        scan. Bean post-processors are force-instantiated (ordered by @Order,
        then name) before cycle detection so they can process every bean —
        including the eager singletons instantiated afterwards.
        """
        if self._scan:
            scan_packages(self, self._scan, self._exclude)
        # Two-phase condition evaluation: phase A drops defs whose class /
        # property / profile / custom conditions fail; phase B then checks
        # bean-presence conditions against the post-A registry so user beans
        # beat conditional ones regardless of registration order and
        # phase-A-dropped defs do not count as present.
        self._evaluate_conditions()
        self._instantiate_post_processors()
        # Aspects are beans too: force-instantiate them (with DI and the BPP
        # phase) before compiling the match table so advice can bind to live
        # instances. Aspect beans themselves are never wrapped.
        self._instantiate_aspects()
        self._build_aop_table()
        # Listener discovery is static (class walk + resolved hints); the
        # listener beans themselves are force-instantiated — ignoring lazy —
        # so a listener nothing depends on still receives events.
        self._build_listener_table()
        self._instantiate_listeners()
        detect_cycles(self._registry)
        for defn in self._registry.all():
            if defn.scope is Scope.SINGLETON and (eager or not defn.lazy):
                self._get_or_create(defn)

    @property
    def condition_report(self) -> ConditionEvaluationReport:
        """Per-definition outcomes of the last refresh's condition evaluation."""
        return self._condition_report

    @property
    def environment(self) -> Environment:
        """The context's property environment."""
        return self._environment

    def _evaluate_conditions(self) -> None:
        """Drop non-matching conditional defs; record every one in the report."""
        report = ConditionEvaluationReport()
        self._condition_report = report
        if not any(defn.conditions for defn in self._registry.all()):
            return
        is_importable = _make_is_importable()
        # Phase A: everything except bean-presence conditions.
        pending: list[tuple[BeanDefinition, list[str]]] = []
        dropped: list[tuple[BeanDefinition, list[str]]] = []
        for defn in self._registry.all():
            if not defn.conditions:
                continue
            ctx = ConditionContext(
                self._environment, self._registry, is_importable, excluding=defn.name
            )
            reasons: list[str] = []
            matched = True
            for condition in defn.conditions:
                if is_bean_presence(condition):
                    continue
                result = evaluate_condition(condition, ctx)
                reasons.append(result.reason)
                matched = matched and result.matched
            if matched:
                pending.append((defn, reasons))
            else:
                dropped.append((defn, reasons))
                self._registry.remove(defn.name)
        # Phase B: bean-presence conditions against the post-A registry.
        for defn, reasons in pending:
            ctx = ConditionContext(
                self._environment, self._registry, is_importable, excluding=defn.name
            )
            matched = True
            for condition in defn.conditions:
                if not is_bean_presence(condition):
                    continue
                result = evaluate_condition(condition, ctx)
                reasons.append(result.reason)
                matched = matched and result.matched
            report.record(defn.name, matched, reasons)
            if not matched:
                self._registry.remove(defn.name)
        # Phase-A drops still record their bean-presence reasons (report-only).
        for defn, reasons in dropped:
            ctx = ConditionContext(
                self._environment, self._registry, is_importable, excluding=defn.name
            )
            for condition in defn.conditions:
                if not is_bean_presence(condition):
                    continue
                reasons.append(evaluate_condition(condition, ctx).reason)
            report.record(defn.name, False, reasons)
        # Dropped defs may be memoized in the resolved-definition cache.
        self._resolved.clear()

    def _instantiate_aspects(self) -> None:
        """Force-create all singleton aspect beans, ordered by (@Order, name)."""
        defs = [
            defn
            for defn in self._registry.all()
            if defn.scope is Scope.SINGLETON and is_aspect(defn.bean_type)
        ]
        defs.sort(key=lambda d: (d.order, d.name))
        for defn in defs:
            self._get_or_create(defn)

    def _aspect_bindings_list(self) -> list[_AdviceBinding]:
        """Collect every advice method declared on registered aspect beans."""
        if self._aspect_bindings is None:
            bindings: list[_AdviceBinding] = []
            for defn in self._registry.all():
                cls = defn.bean_type
                if not is_aspect(cls):
                    continue
                class_order = getattr(cls, ORDER_ATTR, DEFAULT_ORDER)
                if not isinstance(class_order, int):
                    class_order = DEFAULT_ORDER
                for method_name, func in iter_advices(cls):
                    spec: AdviceSpec = getattr(func, ADVICE_ATTR)
                    bindings.append(
                        _AdviceBinding(
                            aspect_name=defn.name,
                            aspect_type=cls,
                            advice_method=method_name,
                            func=func,
                            kind=spec.kind,
                            order=advice_order(func, class_order),
                            pointcut=spec.pointcut,
                        )
                    )
            self._aspect_bindings = bindings
        return self._aspect_bindings

    def _build_aop_table(self) -> None:
        """Precompute ``bean def -> {method: ordered advices}`` for every bean.

        Matching is static (against the bean class), so lazy beans created
        later reuse the cached table. A sync/async mismatch between a matched
        advice and its target method fails here at ``refresh()``.
        """
        for defn in self._registry.all():
            if defn.name not in self._aop_matches:
                self._aop_matches[defn.name] = self._match_defn(defn)

    def _match_defn(
        self, defn: BeanDefinition
    ) -> dict[str, tuple[Callable[..., Any], list[_AdviceBinding]]]:
        cls = defn.bean_type
        if defn.internal or not isinstance(cls, type) or is_aspect(cls):
            return {}
        bindings = self._aspect_bindings_list()
        if not bindings:
            return {}
        matched: dict[str, tuple[Callable[..., Any], list[_AdviceBinding]]] = {}
        for method_name, func in iter_candidate_methods(cls):
            hits = [b for b in bindings if b.pointcut.matches(cls, method_name, func)]
            if not hits:
                continue
            is_async = asyncio.iscoroutinefunction(func)
            for binding in hits:
                advice_is_async = asyncio.iscoroutinefunction(binding.func)
                if advice_is_async != is_async:
                    raise PyleafError(
                        f"Aspect {binding.aspect_type.__name__}."
                        f"{binding.advice_method}: "
                        f"{'async' if advice_is_async else 'sync'} advice"
                        f" cannot intercept {'async' if is_async else 'sync'}"
                        f" method {_type_name(cls)}.{method_name}"
                    )
            hits.sort(key=lambda b: b.order)
            matched[method_name] = (func, hits)
        return matched

    def _compile_link(self, binding: _AdviceBinding) -> Callable[[Invocation], Any]:
        """Bind an advice to its aspect instance and compile a chain link."""
        call: Callable[[Invocation], Any]
        aspect = self._singletons.get(binding.aspect_name)
        if aspect is not None:
            call = getattr(aspect, binding.advice_method)
        else:
            # The aspect is mid-creation (an advised bean is one of its own
            # dependencies): resolve the aspect instance on first invocation.
            def lazy_call(inv: Invocation) -> Any:
                instance = self.get_bean(binding.aspect_name)
                return getattr(instance, binding.advice_method)(inv)

            call = lazy_call
        return compile_link(
            call, binding.kind, asyncio.iscoroutinefunction(binding.func)
        )

    def _apply_aop(self, bean: Any, defn: BeanDefinition) -> Any:
        """Final creation stage: patch matched methods on the instance.

        Interceptors live in the instance dict so self-invocation is
        intercepted too. Beans with no matching pointcut get zero attribute
        writes; ``__slots__`` classes that would match fail loudly.
        """
        matched = self._aop_matches.get(defn.name)
        if matched is None:
            # Definition registered after refresh() (on-demand registration).
            matched = self._aop_matches[defn.name] = self._match_defn(defn)
        if not matched:
            return bean
        for method_name, (func, bindings) in matched.items():
            if not hasattr(bean, method_name):
                continue
            links = [self._compile_link(b) for b in bindings]
            wrapper = make_wrapper(bean, method_name, func, links)
            try:
                setattr(bean, method_name, wrapper)
            except AttributeError as exc:
                raise BeanCreationError(
                    f"Error creating bean '{defn.name}'"
                    f" ({_type_name(defn.bean_type)}): cannot intercept"
                    f" method '{method_name}': instance does not allow"
                    " attribute patching (e.g. __slots__)"
                ) from exc
        return bean

    def _instantiate_post_processors(self) -> None:
        """Force-create all singleton BPP beans, ordered by (@Order, name).

        Processors are appended to ``self._post_processors`` as soon as they
        exist, so an already-created post-processor applies to beans —
        including later post-processors — created after it. Lazy defaults
        are ignored: post-processors must exist before any regular bean.
        """
        defs = [
            defn
            for defn in self._registry.all()
            if defn.scope is Scope.SINGLETON
            and is_bean_post_processor(defn.bean_type)
        ]
        defs.sort(key=lambda d: (d.order, d.name))
        self._post_processors = []
        for defn in defs:
            self._post_processors.append(self._get_or_create(defn))

    def _build_listener_table(self) -> None:
        """Resolve every ``@EventListener`` method into an ordered entry.

        Discovery is static — a class walk plus resolved type hints — so no
        bean is instantiated here. Entries sort by (@Order, bean name); the
        sort is stable, so same-bean listeners keep declaration order.
        """
        entries: list[ListenerEntry] = []
        for defn in self._registry.all():
            cls = defn.bean_type
            if defn.internal or not isinstance(cls, type):
                continue
            for method_name, func in iter_event_listeners(cls):
                owner = f"{_type_name(cls)}.{method_name}"
                specs = callable_dependencies(func, owner, self._registry)
                if len(specs) != 1:
                    raise BeanCreationError(
                        f"@EventListener method '{owner}' must declare"
                        f" exactly one event parameter (got {len(specs)})"
                    )
                order = getattr(func, ORDER_ATTR, defn.order)
                if not isinstance(order, int):
                    order = defn.order
                entries.append(
                    ListenerEntry(
                        bean_name=defn.name,
                        method_name=method_name,
                        event_type=specs[0].bean_type,
                        is_async=asyncio.iscoroutinefunction(func),
                        order=order,
                    )
                )
        entries.sort(key=lambda e: (e.order, e.bean_name))
        self._listeners = entries
        self._listeners_dirty = False

    def _instantiate_listeners(self) -> None:
        """Force-create all singleton listener beans, ordered by (@Order, name).

        Like post-processors and aspects, listener beans must exist before
        regular beans: lazy defaults are ignored so a listener nothing
        injects still receives events.
        """
        names = {entry.bean_name for entry in self._listeners}
        defs = [
            defn
            for defn in self._registry.all()
            if defn.name in names and defn.scope is Scope.SINGLETON
        ]
        defs.sort(key=lambda d: (d.order, d.name))
        for defn in defs:
            self._get_or_create(defn)

    @overload
    def get(self, required: type[T]) -> T: ...

    @overload
    def get(self, required: Any) -> Any: ...

    def get(self, required: Any) -> Any:
        """Resolve a bean by type; accepts ``Annotated[T, Qualifier("name")]``."""
        try:
            defn = self._resolved.get(required)
        except TypeError:
            # ``required`` is unhashable; resolve without caching.
            defn = None
        if defn is None:
            bean_type, qualifier = peel_annotated(required)
            defn = self._resolve_single(bean_type, qualifier, allow_fallback=True)
            try:
                # Cache the resolved definition, not the instance: prototype
                # beans still get a fresh instance per call.
                self._resolved[required] = defn
            except TypeError:
                pass
        return self._get_or_create(defn)

    @overload
    def get_all(self, required: type[T]) -> list[T]: ...

    @overload
    def get_all(self, required: Any) -> list[Any]: ...

    def get_all(self, required: Any) -> list[Any]:
        """Return all beans assignable to ``required``, ordered by @Order, then name."""
        bean_type, qualifier = peel_annotated(required)
        cands = self._registry.candidates(bean_type)
        if qualifier is not None:
            cands = [c for c in cands if c.name == qualifier]
        cands.sort(key=lambda d: (d.order, d.name))
        return [self._get_or_create(c) for c in cands]

    def get_bean(self, name: str) -> Any:
        """Resolve a bean by its registered name."""
        return self._get_or_create(self._registry.get(name))

    def publish(self, event: object) -> None:
        """Dispatch ``event`` to every matching listener, in listener order.

        Sync listeners are invoked directly; a throwing listener is logged
        and does not prevent later listeners (publish itself never raises
        for a listener failure). Async listeners are scheduled on the
        running loop when there is one, else run via ``asyncio.run``.
        """
        if self._listeners_dirty:
            self._build_listener_table()
        event_type = type(event)
        for entry in self._listeners:
            if not event_matches(event_type, entry.event_type):
                continue
            call = getattr(self.get_bean(entry.bean_name), entry.method_name)
            if entry.is_async:
                self._schedule_listener(call, entry, event)
            else:
                try:
                    call(event)
                except Exception:
                    _logger.exception(
                        "Event listener %s.%s raised handling %r",
                        entry.bean_name,
                        entry.method_name,
                        event,
                    )

    async def publish_async(self, event: object) -> None:
        """Dispatch ``event`` like :meth:`publish`, awaiting async listeners.

        Listeners run sequentially in listener order — sync called inline,
        async awaited — so mixed listeners keep a deterministic order.
        """
        if self._listeners_dirty:
            self._build_listener_table()
        event_type = type(event)
        for entry in self._listeners:
            if not event_matches(event_type, entry.event_type):
                continue
            call = getattr(self.get_bean(entry.bean_name), entry.method_name)
            try:
                if entry.is_async:
                    await call(event)
                else:
                    call(event)
            except Exception:
                _logger.exception(
                    "Event listener %s.%s raised handling %r",
                    entry.bean_name,
                    entry.method_name,
                    event,
                )

    def _schedule_listener(
        self,
        call: Callable[..., Any],
        entry: ListenerEntry,
        event: object,
    ) -> None:
        """Run an async listener from sync ``publish``.

        Scheduled as a task on the running loop when one exists, else run to
        completion via ``asyncio.run``. The wrapper catches and logs listener
        failures so they never propagate to the publisher or surface as
        unretrieved task errors.
        """

        async def run() -> None:
            try:
                await call(event)
            except Exception:
                _logger.exception(
                    "Event listener %s.%s raised handling %r",
                    entry.bean_name,
                    entry.method_name,
                    event,
                )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            asyncio.run(run())
        else:
            loop.create_task(run())

    def close(self) -> None:
        """Destroy singletons (reverse creation order), then drop the caches.

        ``@PreDestroy`` hooks run on singleton beans only; prototype instances
        are never destroyed. A failing hook is logged and does not prevent the
        remaining beans from being destroyed.
        """
        for instance in reversed(self._created):
            for method_name in lifecycle_methods(type(instance), PRE_DESTROY_ATTR):
                try:
                    getattr(instance, method_name)()
                except Exception:
                    _logger.exception(
                        "Error running @PreDestroy '%s' on bean %r",
                        method_name,
                        instance,
                    )
        self._created.clear()
        self._singletons.clear()

    def _resolve_single(
        self,
        bean_type: Any,
        qualifier: str | None,
        *,
        allow_fallback: bool,
    ) -> BeanDefinition:
        cands = self._registry.candidates(bean_type)
        if (
            allow_fallback
            and not cands
            and isinstance(bean_type, type)
            and (
                getattr(bean_type, COMPONENT_ATTR, None) is not None
                or getattr(bean_type, CONFIGURATION_ATTR, None) is not None
                or getattr(bean_type, CONFIG_PROPS_ATTR, None) is not None
            )
        ):
            # A single dependency on a component that was never registered is
            # registered on demand (it may live in another module).
            self.register(bean_type)
            cands = self._registry.candidates(bean_type)

        required = _type_name(bean_type)
        if qualifier is not None:
            matches = [c for c in cands if c.name == qualifier]
            if not matches:
                raise NoSuchBean(
                    f"No bean named '{qualifier}' assignable to type '{required}'"
                )
            return matches[0]
        if not cands:
            raise NoSuchBean(f"No bean of type '{required}'")
        if len(cands) == 1:
            return cands[0]
        primaries = [c for c in cands if c.primary]
        if len(primaries) == 1:
            return primaries[0]
        listed = ", ".join(
            f"{c.name} ({_type_name(c.bean_type)})" for c in cands
        )
        raise NoUniqueBean(f"Multiple beans of type '{required}': {listed}")

    def _inject(self, spec: DepSpec) -> Any:
        if spec.kind is DepKind.LIST:
            cands = self._registry.candidates(spec.bean_type)
            if spec.qualifier is not None:
                cands = [c for c in cands if c.name == spec.qualifier]
            cands.sort(key=lambda d: (d.order, d.name))
            return [self._get_or_create(c) for c in cands]
        try:
            defn = self._resolve_single(
                spec.bean_type,
                spec.qualifier,
                allow_fallback=spec.kind is DepKind.SINGLE,
            )
        except NoSuchBean:
            if spec.kind is DepKind.OPTIONAL:
                return None
            raise
        return self._get_or_create(defn)

    def _get_or_create(self, defn: BeanDefinition) -> Any:
        if defn.scope is Scope.SINGLETON and defn.name in self._singletons:
            return self._singletons[defn.name]
        if defn.name in self._creating:
            names = [
                _type_name(self._registry.get(n).bean_type)
                for n in [*self._creating, defn.name]
            ]
            raise CircularDependency(
                "Circular dependency detected: " + " -> ".join(names)
            )
        self._creating.append(defn.name)
        try:
            instance = self._apply_creation_pipeline(defn.factory(), defn)
        except PyleafError:
            raise
        except Exception as exc:
            raise BeanCreationError(
                f"Error creating bean '{defn.name}'"
                f" ({_type_name(defn.bean_type)}):"
                f" {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            self._creating.remove(defn.name)
        if defn.scope is Scope.SINGLETON:
            self._singletons[defn.name] = instance
            # Record the actual creation moment (lazy singletons create out
            # of registration order) so close() can destroy in reverse.
            self._created.append(instance)
        return instance

    def _apply_creation_pipeline(self, bean: Any, defn: BeanDefinition) -> Any:
        """Run the post-construction chain:
        BPP before -> @PostConstruct -> BPP after -> AOP method patching.

        A post-processor may return a different object; the replacement is
        what later processors, ``@PostConstruct`` discovery and the singleton
        cache see. A processor is never applied to itself.
        """
        name = defn.name
        for bpp in self._post_processors:
            hook = getattr(bpp, BEFORE_INIT_HOOK, None)
            if callable(hook) and bpp is not bean:
                bean = hook(bean, name)
        for method_name in lifecycle_methods(type(bean), POST_CONSTRUCT_ATTR):
            getattr(bean, method_name)()
        for bpp in self._post_processors:
            hook = getattr(bpp, AFTER_INIT_HOOK, None)
            if callable(hook) and bpp is not bean:
                bean = hook(bean, name)
        return self._apply_aop(bean, defn)
