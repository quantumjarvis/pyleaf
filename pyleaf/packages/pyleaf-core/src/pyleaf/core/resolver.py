"""Type-hint resolution and static dependency-graph cycle detection."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from types import UnionType
from typing import Annotated, Any, Optional, Union, get_args, get_origin, get_type_hints

from .definition import BeanDefinition, BeanRegistry
from .errors import BeanCreationError, CircularDependency
from .stereotypes import Qualifier

TYPING_NAMES: dict[str, Any] = {
    "Annotated": Annotated,
    "Optional": Optional,
    "Union": Union,
    "Qualifier": Qualifier,
}


class DepKind(Enum):
    """Kind of an injection point."""

    SINGLE = auto()
    OPTIONAL = auto()
    LIST = auto()


@dataclass(frozen=True)
class DepSpec:
    """A resolved dependency: parameter name, kind, required type and qualifier."""

    name: str
    kind: DepKind
    bean_type: Any
    qualifier: str | None = None


def peel_annotated(ann: Any) -> tuple[Any, str | None]:
    """Peel ``Annotated`` wrappers; return (base type, qualifier name or None)."""
    qualifier: str | None = None
    while get_origin(ann) is Annotated:
        args = get_args(ann)
        for meta in args[1:]:
            if isinstance(meta, Qualifier) and qualifier is None:
                qualifier = meta.name
        ann = args[0]
    return ann, qualifier


def _registry_localns(registry: BeanRegistry | None) -> dict[str, Any]:
    """Namespace mapping registered bean type names to the types themselves."""
    lns: dict[str, Any] = dict(TYPING_NAMES)
    if registry is not None:
        for defn in registry.all():
            bean_type = defn.bean_type
            if isinstance(bean_type, type):
                lns.setdefault(bean_type.__name__, bean_type)
    return lns


def _globalns(func: Callable[..., Any]) -> dict[str, Any]:
    gns = getattr(func, "__globals__", None)
    if isinstance(gns, dict):
        return dict(gns)
    return {}


def _resolve_hints(
    func: Callable[..., Any],
    owner: str,
    registry: BeanRegistry | None,
) -> dict[str, Any]:
    """Resolve type hints, falling back to registered bean names on NameError."""
    try:
        return get_type_hints(func, include_extras=True)
    except NameError:
        pass
    except Exception as exc:
        raise BeanCreationError(f"Cannot inspect type hints of {owner}: {exc}") from exc

    # Fallback: a hint may name a class invisible to module globals (typical:
    # a string annotation on a class defined inside a function). Resolve bare
    # names against the registered bean types.
    gns = _globalns(func)
    lns = _registry_localns(registry)
    try:
        return get_type_hints(func, gns, lns, include_extras=True)
    except Exception as exc:
        merged = {**gns, **lns}
        for pname, param in inspect.signature(func).parameters.items():
            ann = param.annotation
            if isinstance(ann, str):
                try:
                    # Annotation strings come from modules the app itself
                    # loads; trust level is that of executing module code.
                    eval(ann, merged)  # noqa: S307  # nosec B307
                except Exception as inner:
                    raise BeanCreationError(
                        f"Cannot resolve type hint for parameter '{pname}'"
                        f" of {owner}: {inner}"
                    ) from inner
        raise BeanCreationError(f"Cannot resolve type hints of {owner}: {exc}") from exc


def _make_union(types: list[Any]) -> Any:
    result: Any = types[0]
    for tp in types[1:]:
        result = result | tp
    return result


def _classify(name: str, ann: Any) -> DepSpec:
    ann, qualifier = peel_annotated(ann)
    origin = get_origin(ann)
    if origin is Union or origin is UnionType:
        args = get_args(ann)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) < len(args):
            inner: Any = non_none[0] if len(non_none) == 1 else _make_union(non_none)
            inner, inner_q = peel_annotated(inner)
            return DepSpec(name, DepKind.OPTIONAL, inner, qualifier or inner_q)
        return DepSpec(name, DepKind.SINGLE, ann, qualifier)
    if origin is list:
        args = get_args(ann)
        elem: Any = args[0] if args else object
        elem, elem_q = peel_annotated(elem)
        return DepSpec(name, DepKind.LIST, elem, qualifier or elem_q)
    return DepSpec(name, DepKind.SINGLE, ann, qualifier)


def _params_dependencies(
    func: Callable[..., Any],
    owner: str,
    registry: BeanRegistry | None,
) -> list[DepSpec]:
    hints = _resolve_hints(func, owner, registry)
    specs: list[DepSpec] = []
    for pname, param in inspect.signature(func).parameters.items():
        if pname in ("self", "cls"):
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        ann = hints.get(pname, param.annotation)
        if ann is inspect.Parameter.empty:
            raise BeanCreationError(
                f"Parameter '{pname}' of {owner} has no type annotation"
            )
        if isinstance(ann, str):
            merged = {**_globalns(func), **_registry_localns(registry)}
            try:
                # Annotation strings come from modules the app itself loads;
                # trust level is that of executing module code.
                ann = eval(ann, merged)  # noqa: S307  # nosec B307
            except Exception as exc:
                raise BeanCreationError(
                    f"Cannot resolve type hint for parameter '{pname}'"
                    f" of {owner}: {exc}"
                ) from exc
        try:
            specs.append(_classify(pname, ann))
        except Exception as exc:
            raise BeanCreationError(
                f"Cannot classify type hint '{ann}' for parameter '{pname}'"
                f" of {owner}: {exc}"
            ) from exc
    return specs


def constructor_dependencies(
    cls: type[Any],
    registry: BeanRegistry | None = None,
) -> list[DepSpec]:
    """Return the dependency specs of ``cls.__init__`` parameters."""
    init = cls.__init__
    if init is object.__init__:
        return []
    return _params_dependencies(init, f"{cls.__name__}.__init__", registry)


def callable_dependencies(
    func: Callable[..., Any],
    owner: str,
    registry: BeanRegistry | None,
) -> list[DepSpec]:
    """Return the dependency specs of an arbitrary callable's parameters."""
    return _params_dependencies(func, owner, registry)


def bean_return_type(
    func: Callable[..., Any],
    owner: str,
    registry: BeanRegistry | None,
) -> Any:
    """Resolve a ``@Bean`` method's return annotation without calling it."""
    hints = _resolve_hints(func, owner, registry)
    if "return" in hints:
        return hints["return"]
    raise BeanCreationError(f"@Bean method '{owner}' has no return type annotation")


def _display_name(defn: BeanDefinition) -> str:
    name = getattr(defn.bean_type, "__name__", None)
    return name if isinstance(name, str) else str(defn.bean_type)


def _edge_targets(spec: DepSpec, registry: BeanRegistry) -> list[str]:
    """Bean names this dep could resolve to, used for static cycle detection."""
    cands = registry.candidates(spec.bean_type)
    if spec.qualifier is not None:
        return [c.name for c in cands if c.name == spec.qualifier]
    if spec.kind is DepKind.LIST:
        return [c.name for c in cands]
    if len(cands) == 1:
        return [cands[0].name]
    primaries = [c for c in cands if c.primary]
    if len(primaries) == 1:
        return [primaries[0].name]
    return [c.name for c in cands]


def detect_cycles(registry: BeanRegistry) -> None:
    """DFS over the static dependency graph; raise CircularDependency on a cycle.

    Runs purely on type signatures (component constructors and @Bean method
    parameters), before any instantiation.
    """
    defs = registry.all()
    by_name = {d.name: d for d in defs}
    graph: dict[str, list[str]] = {}
    for defn in defs:
        edges: list[str] = []
        for spec in defn.dep_specs():
            edges.extend(_edge_targets(spec, registry))
        graph[defn.name] = edges

    color: dict[str, int] = {}  # absent = unvisited, 1 = on stack, 2 = done
    stack: list[str] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            state = color.get(nxt, 0)
            if state == 1:
                idx = stack.index(nxt)
                path = [*stack[idx:], nxt]
                names = [_display_name(by_name[n]) for n in path]
                raise CircularDependency(
                    "Circular dependency detected: " + " -> ".join(names)
                )
            if state == 0:
                visit(nxt)
        stack.pop()
        color[node] = 2

    for defn in defs:
        if color.get(defn.name, 0) == 0:
            visit(defn.name)


__all__ = [
    "DepKind",
    "DepSpec",
    "bean_return_type",
    "callable_dependencies",
    "constructor_dependencies",
    "detect_cycles",
    "peel_annotated",
]
