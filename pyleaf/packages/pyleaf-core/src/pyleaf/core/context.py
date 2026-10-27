"""The pyleaf application context: registration, resolution, lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, TypeVar, overload

from .definition import DEFAULT_ORDER, BeanDefinition, BeanRegistry, Scope
from .errors import (
    BeanCreationError,
    CircularDependency,
    NoSuchBean,
    NoUniqueBean,
    PyleafError,
)
from .resolver import (
    DepKind,
    DepSpec,
    bean_return_type,
    callable_dependencies,
    constructor_dependencies,
    detect_cycles,
    peel_annotated,
)
from .stereotypes import (
    BEAN_ATTR,
    COMPONENT_ATTR,
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


def _type_name(tp: Any) -> str:
    name = getattr(tp, "__name__", None)
    return name if isinstance(name, str) else str(tp)


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

    def __init__(self) -> None:
        self._registry = BeanRegistry()
        self._singletons: dict[str, Any] = {}
        self._creating: list[str] = []
        self._resolved: dict[Any, BeanDefinition] = {}

    def register(self, cls: type[T]) -> type[T]:
        """Register a component-stereotype or ``@Configuration`` class."""
        # New definitions may change the outcome of earlier resolutions
        # (extra candidate, new @Primary, qualifier match), so cached
        # definition lookups are stale.
        self._resolved.clear()
        if getattr(cls, COMPONENT_ATTR, None) is not None:
            self._register_component(cls)
        elif getattr(cls, CONFIGURATION_ATTR, None) is not None:
            self._register_configuration(cls)
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
        )
        self._registry.register(config_defn)
        for method_name, func in _iter_bean_methods(cls):
            self._register_bean_method(cls, config_name, method_name, func)

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
        """Validate the static dependency graph, then pre-instantiate singletons.

        Cycle detection runs before any instantiation. Afterwards, singletons
        marked ``@Lazy(False)`` — and every singleton when ``eager=True`` —
        are instantiated.
        """
        detect_cycles(self._registry)
        for defn in self._registry.all():
            if defn.scope is Scope.SINGLETON and (eager or not defn.lazy):
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

    def close(self) -> None:
        """Drop all cached singletons."""
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
            instance = defn.factory()
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
        return instance
