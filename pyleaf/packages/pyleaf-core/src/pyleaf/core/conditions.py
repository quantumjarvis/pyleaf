"""Conditional bean registration: the @ConditionalOn* family and the report.

Condition decorators mark component classes and ``@Bean`` methods; the marks
are collected onto each :class:`~pyleaf.core.definition.BeanDefinition` at
``register()`` time and evaluated during ``refresh()`` in two phases:

- phase A evaluates everything except bean-presence conditions
  (@ConditionalOnBean / @ConditionalOnMissingBean) and drops non-matching
  definitions;
- phase B evaluates bean-presence conditions against the post-A registry, so
  unconditionally registered user beans always win over conditional ones
  regardless of registration order, and phase-A-dropped definitions do not
  count as present.

Every conditional definition — matched or not — is recorded in
``ctx.condition_report`` with one human-readable reason per condition.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Protocol,
    TypeVar,
    cast,
    runtime_checkable,
)

from .definition import BeanRegistry

if TYPE_CHECKING:
    from .environment import Environment

CONDITIONS_ATTR = "__pyleaf_conditions__"

F = TypeVar("F")


def _type_name(tp: Any) -> str:
    name = getattr(tp, "__name__", None)
    return name if isinstance(name, str) else str(tp)


@dataclass(frozen=True)
class ConditionContext:
    """What a condition may inspect during ``refresh()`` evaluation."""

    environment: Environment
    registry: BeanRegistry
    is_importable: Callable[[str], bool]
    # Name of the definition currently being evaluated; bean-presence
    # conditions ignore it so a bean never counts as its own match.
    excluding: str | None = None


@dataclass(frozen=True)
class ConditionResult:
    """Outcome of one condition evaluation, with a human-readable reason."""

    matched: bool
    reason: str


@runtime_checkable
class Condition(Protocol):
    """A predicate evaluated against the registry/environment at refresh."""

    def matches(self, ctx: ConditionContext) -> ConditionResult | bool:
        """Return whether the condition holds; bools get a generic reason."""
        ...


def is_bean_presence(condition: Any) -> bool:
    """True when ``condition`` inspects bean presence (phase-B evaluation)."""
    return bool(getattr(condition, "bean_presence", False))


def evaluate_condition(condition: Any, ctx: ConditionContext) -> ConditionResult:
    """Run ``condition.matches`` and normalize bool returns to a result."""
    result = condition.matches(ctx)
    if isinstance(result, ConditionResult):
        return result
    matched = bool(result)
    name = type(condition).__name__
    return ConditionResult(matched, f"{name} {'matched' if matched else 'did not match'}")


def conditions_of(target: Any) -> list[Condition]:
    """Return the conditions marked on ``target`` (class or @Bean method)."""
    return list(getattr(target, CONDITIONS_ATTR, ()))


def _add_condition(target: F, condition: Condition) -> F:
    existing = getattr(target, CONDITIONS_ATTR, None)
    if existing is None:
        existing = []
        setattr(target, CONDITIONS_ATTR, existing)
    elif CONDITIONS_ATTR not in getattr(target, "__dict__", {}):
        # Inherited marker list: copy so the parent's list is not mutated.
        existing = list(existing)
        setattr(target, CONDITIONS_ATTR, existing)
    existing.append(condition)
    return target


# --- Built-in condition implementations ---------------------------------------


class OnClassCondition:
    """Matches when a dotted module/class path is importable.

    For ``a.b.C`` the parent module ``a.b`` is probed first (``C`` is assumed
    to be a class inside it); the full name is tried as a module path as a
    fallback. Probing uses ``importlib.util.find_spec`` — modules are never
    executed.
    """

    bean_presence: ClassVar[bool] = False

    def __init__(self, name: str) -> None:
        self.name = name

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        parent, _, _ = self.name.rpartition(".")
        matched = bool(parent and ctx.is_importable(parent)) or ctx.is_importable(self.name)
        verdict = "is present" if matched else "is not importable"
        return ConditionResult(matched, f"@ConditionalOnClass '{self.name}' {verdict}")


class OnMissingClassCondition:
    """Negation of :class:`OnClassCondition`."""

    bean_presence: ClassVar[bool] = False

    def __init__(self, name: str) -> None:
        self.name = name

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        parent, _, _ = self.name.rpartition(".")
        present = bool(parent and ctx.is_importable(parent)) or ctx.is_importable(self.name)
        verdict = "is importable" if present else "is absent"
        return ConditionResult(not present, f"@ConditionalOnMissingClass '{self.name}' {verdict}")


class OnPropertyCondition:
    """Matches on an environment property's presence or value."""

    bean_presence: ClassVar[bool] = False

    def __init__(
        self,
        key: str,
        having_value: str | None = None,
        match_if_missing: bool = False,
    ) -> None:
        self.key = key
        self.having_value = having_value
        self.match_if_missing = match_if_missing

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        expected = f" = '{self.having_value}'" if self.having_value is not None else ""
        label = f"@ConditionalOnProperty '{self.key}'{expected}"
        value = ctx.environment.get(self.key)
        if value is None:
            matched = self.match_if_missing
            reason = (
                f"{label} matched (property missing, match_if_missing=True)"
                if matched
                else f"{label} did not match (property missing)"
            )
            return ConditionResult(matched, reason)
        if self.having_value is None:
            return ConditionResult(True, f"{label} matched (property is present)")
        matched = str(value) == str(self.having_value)
        verdict = "matched" if matched else f"did not match '{value}'"
        return ConditionResult(matched, f"{label} {verdict}")


class OnBeanCondition:
    """Matches when at least one other definition is assignable to a type."""

    bean_presence: ClassVar[bool] = True

    def __init__(self, bean_type: Any) -> None:
        self.bean_type = bean_type

    def _candidates(self, ctx: ConditionContext) -> list[str]:
        return [
            defn.name
            for defn in ctx.registry.candidates(self.bean_type)
            if defn.name != ctx.excluding
        ]

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        found = self._candidates(ctx)
        matched = bool(found)
        names = ", ".join(found) if found else "none"
        return ConditionResult(
            matched,
            f"@ConditionalOnBean {_type_name(self.bean_type)} found beans: {names}",
        )


class OnMissingBeanCondition:
    """Matches when no other definition is assignable to a type."""

    bean_presence: ClassVar[bool] = True

    def __init__(self, bean_type: Any) -> None:
        self.bean_type = bean_type

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        found = [
            defn.name
            for defn in ctx.registry.candidates(self.bean_type)
            if defn.name != ctx.excluding
        ]
        matched = not found
        names = ", ".join(found) if found else "none"
        return ConditionResult(
            matched,
            f"@ConditionalOnMissingBean {_type_name(self.bean_type)} found beans: {names}",
        )


class OnProfileCondition:
    """Matches when at least one of the profiles is active."""

    bean_presence: ClassVar[bool] = False

    def __init__(self, profiles: tuple[str, ...]) -> None:
        self.profiles = profiles

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        active = ctx.environment.active_profiles
        matched = any(profile in active for profile in self.profiles)
        wanted = ", ".join(self.profiles)
        verdict = "matched" if matched else "did not match"
        return ConditionResult(
            matched,
            f"@ConditionalOnProfile [{wanted}] {verdict}"
            f" (active: {', '.join(sorted(active))})",
        )


# --- Condition decorators -----------------------------------------------------


def _decorator(condition: Condition) -> Callable[[F], F]:
    def wrap(target: F) -> F:
        return _add_condition(target, condition)

    return wrap


def ConditionalOnClass(name: str) -> Callable[[F], F]:
    """Match when the dotted module/class path is importable."""
    return _decorator(OnClassCondition(name))


def ConditionalOnMissingClass(name: str) -> Callable[[F], F]:
    """Match when the dotted module/class path is NOT importable."""
    return _decorator(OnMissingClassCondition(name))


def ConditionalOnProperty(
    key: str,
    having_value: str | None = None,
    match_if_missing: bool = False,
) -> Callable[[F], F]:
    """Match on a property's presence (or exact value via ``having_value``)."""
    return _decorator(OnPropertyCondition(key, having_value, match_if_missing))


def ConditionalOnBean(bean_type: Any) -> Callable[[F], F]:
    """Match when a bean assignable to ``bean_type`` is registered."""
    return _decorator(OnBeanCondition(bean_type))


def ConditionalOnMissingBean(bean_type: Any) -> Callable[[F], F]:
    """Match when NO bean assignable to ``bean_type`` is registered."""
    return _decorator(OnMissingBeanCondition(bean_type))


def ConditionalOnProfile(*profiles: str) -> Callable[[F], F]:
    """Match when at least one of ``profiles`` is active."""
    return _decorator(OnProfileCondition(profiles))


def Profile(*profiles: str) -> Callable[[F], F]:
    """Sugar for :func:`ConditionalOnProfile`."""
    return ConditionalOnProfile(*profiles)


def Conditional(condition: Condition | type[Condition]) -> Callable[[F], F]:
    """Attach a custom condition class (instantiated with no args) or instance."""
    if isinstance(condition, type):
        instance = cast(Callable[[], Condition], condition)()
    else:
        instance = condition
    return _decorator(instance)


# --- Evaluation report --------------------------------------------------------


@dataclass
class ConditionReportEntry:
    """Per-definition outcome: every condition's reason, and the verdict."""

    bean_name: str
    matched: bool
    reasons: list[str] = field(default_factory=list)


class ConditionEvaluationReport:
    """Report of every conditional definition evaluated during refresh()."""

    def __init__(self) -> None:
        self.entries: list[ConditionReportEntry] = []

    def record(self, bean_name: str, matched: bool, reasons: list[str]) -> None:
        """Append an entry for ``bean_name``."""
        self.entries.append(ConditionReportEntry(bean_name, matched, list(reasons)))

    def __repr__(self) -> str:
        return f"ConditionEvaluationReport({len(self.entries)} entries)"
