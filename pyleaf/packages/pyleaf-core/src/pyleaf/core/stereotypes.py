"""Stereotype decorators for the pyleaf DI container."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, overload

from .definition import Scope

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

COMPONENT_ATTR = "__pyleaf_component__"
CONFIGURATION_ATTR = "__pyleaf_configuration__"
BEAN_ATTR = "__pyleaf_bean__"
PRIMARY_ATTR = "__pyleaf_primary__"
LAZY_ATTR = "__pyleaf_lazy__"
ORDER_ATTR = "__pyleaf_order__"


def decapitalize(name: str) -> str:
    """Decapitalize the first character: ``OrderService`` -> ``orderService``."""
    return name[:1].lower() + name[1:] if name else name


@dataclass(frozen=True)
class ComponentMeta:
    """Metadata carried by a component-stereotype class."""

    name: str | None
    scope: Scope
    lazy: bool


@dataclass(frozen=True)
class ConfigurationMeta:
    """Metadata carried by a configuration class."""

    name: str | None


@dataclass(frozen=True)
class BeanMeta:
    """Metadata carried by a @Bean method."""

    name: str | None


def _resolve_target(
    target: type[T] | str | None,
    wrap: Callable[[type[T]], type[T]],
) -> type[T] | Callable[[type[T]], type[T]]:
    """Dispatch bare ``@Deco`` vs ``@Deco(...)`` / ``@Deco("name")`` usage."""
    if target is None or isinstance(target, str):
        return wrap
    return wrap(target)


def _stereotype(
    target: type[T] | str | None,
    *,
    name: str | None,
    scope: Scope,
    lazy: bool,
) -> type[T] | Callable[[type[T]], type[T]]:
    effective = name if name is not None else (target if isinstance(target, str) else None)

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, COMPONENT_ATTR, ComponentMeta(effective, scope, lazy))
        return cls

    return _resolve_target(target, wrap)


@overload
def Component(target: type[T], /) -> type[T]: ...


@overload
def Component(
    target: str | None = None,
    /,
    *,
    name: str | None = None,
    scope: Scope = Scope.SINGLETON,
    lazy: bool = True,
) -> Callable[[type[T]], type[T]]: ...


def Component(
    target: type[T] | str | None = None,
    /,
    *,
    name: str | None = None,
    scope: Scope = Scope.SINGLETON,
    lazy: bool = True,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Mark a class as a pyleaf component (usable bare or with arguments)."""
    return _stereotype(target, name=name, scope=scope, lazy=lazy)


@overload
def Service(target: type[T], /) -> type[T]: ...


@overload
def Service(
    target: str | None = None,
    /,
    *,
    name: str | None = None,
    scope: Scope = Scope.SINGLETON,
    lazy: bool = True,
) -> Callable[[type[T]], type[T]]: ...


def Service(
    target: type[T] | str | None = None,
    /,
    *,
    name: str | None = None,
    scope: Scope = Scope.SINGLETON,
    lazy: bool = True,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Service stereotype; same semantics as :func:`Component`."""
    return _stereotype(target, name=name, scope=scope, lazy=lazy)


@overload
def Repository(target: type[T], /) -> type[T]: ...


@overload
def Repository(
    target: str | None = None,
    /,
    *,
    name: str | None = None,
    scope: Scope = Scope.SINGLETON,
    lazy: bool = True,
) -> Callable[[type[T]], type[T]]: ...


def Repository(
    target: type[T] | str | None = None,
    /,
    *,
    name: str | None = None,
    scope: Scope = Scope.SINGLETON,
    lazy: bool = True,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Repository stereotype; same semantics as :func:`Component`."""
    return _stereotype(target, name=name, scope=scope, lazy=lazy)


@overload
def Controller(target: type[T], /) -> type[T]: ...


@overload
def Controller(
    target: str | None = None,
    /,
    *,
    name: str | None = None,
    scope: Scope = Scope.SINGLETON,
    lazy: bool = True,
) -> Callable[[type[T]], type[T]]: ...


def Controller(
    target: type[T] | str | None = None,
    /,
    *,
    name: str | None = None,
    scope: Scope = Scope.SINGLETON,
    lazy: bool = True,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Controller stereotype; same semantics as :func:`Component`."""
    return _stereotype(target, name=name, scope=scope, lazy=lazy)


@overload
def Configuration(target: type[T], /) -> type[T]: ...


@overload
def Configuration(
    target: str | None = None,
    /,
    *,
    name: str | None = None,
) -> Callable[[type[T]], type[T]]: ...


def Configuration(
    target: type[T] | str | None = None,
    /,
    *,
    name: str | None = None,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Mark a class as a configuration source of ``@Bean`` methods."""
    effective = name if name is not None else (target if isinstance(target, str) else None)

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, CONFIGURATION_ATTR, ConfigurationMeta(effective))
        return cls

    return _resolve_target(target, wrap)


@overload
def Bean(target: F, /) -> F: ...


@overload
def Bean(
    target: str | None = None,
    /,
    *,
    name: str | None = None,
) -> Callable[[F], F]: ...


def Bean(
    target: F | str | None = None,
    /,
    *,
    name: str | None = None,
) -> F | Callable[[F], F]:
    """Mark a ``@Configuration`` method as a bean factory (bare or with ``name=``)."""
    effective = name if name is not None else (target if isinstance(target, str) else None)

    def wrap(func: F) -> F:
        setattr(func, BEAN_ATTR, BeanMeta(effective))
        return func

    if target is None or isinstance(target, str):
        return wrap
    return wrap(target)


def Primary(cls: type[T]) -> type[T]:
    """Mark a bean as the primary candidate for its type."""
    setattr(cls, PRIMARY_ATTR, True)
    return cls


@overload
def Lazy(value: type[T]) -> type[T]: ...


@overload
def Lazy(value: bool = True) -> Callable[[type[T]], type[T]]: ...


def Lazy(value: bool | type[T] = True) -> type[T] | Callable[[type[T]], type[T]]:
    """``@Lazy(False)`` marks a singleton eager; ``@Lazy``/``@Lazy(True)`` is the default."""
    if isinstance(value, type):
        setattr(value, LAZY_ATTR, True)
        return value

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, LAZY_ATTR, value)
        return cls

    return wrap


def Order(value: int) -> Callable[[type[T]], type[T]]:
    """``@Order(n)`` orders beans inside ``list[T]`` injection; smaller comes first."""

    def wrap(cls: type[T]) -> type[T]:
        setattr(cls, ORDER_ATTR, value)
        return cls

    return wrap


class Qualifier:
    """Bean-name qualifier used as ``Annotated`` metadata at injection points."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Qualifier) and self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __repr__(self) -> str:
        return f"Qualifier({self.name!r})"
