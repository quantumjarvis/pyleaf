"""``@ConfigurationProperties``: bind environment keys to pydantic models.

A marked ``BaseModel`` registers directly on ``ctx.register`` — no
``@Component`` needed. The decorator attaches a
:class:`ConfigPropertiesMeta` under ``__pyleaf_configprops__``;
``ApplicationContext.register`` dispatches on that attribute and calls
``meta.build_definition(ctx, cls)``, so the binding logic lives here in
boot while the container treats the result as an ordinary singleton def.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, get_args

from pydantic import BaseModel, SecretStr, ValidationError
from pyleaf.core.conditions import conditions_of
from pyleaf.core.definition import BeanDefinition, Scope
from pyleaf.core.environment import Environment
from pyleaf.core.errors import BeanCreationError, PyleafError
from pyleaf.core.stereotypes import CONFIG_PROPS_ATTR, decapitalize

if TYPE_CHECKING:
    from pyleaf.core.context import ApplicationContext

T = TypeVar("T", bound=BaseModel)


def collect_prefixed(env: Environment, prefix: str) -> dict[str, Any]:
    """Unflatten every env key under ``prefix.`` into a nested dict.

    Sources apply lowest-to-highest precedence, so a later source's leaf
    replaces an earlier value (a scalar replaces a whole subtree).
    """
    stem = prefix + "."
    out: dict[str, Any] = {}
    for source in env.sources:
        for key, value in source.values.items():
            if not key.startswith(stem):
                continue
            path = key[len(stem) :].split(".")
            node = out
            for part in path[:-1]:
                child = node.get(part)
                if not isinstance(child, dict):
                    child = {}
                    node[part] = child
                node = child
            node[path[-1]] = value
    return out


def _format_error(error: Mapping[str, Any]) -> str:
    loc = ".".join(str(part) for part in error.get("loc", ()))
    return f"property '{loc}': {error.get('msg')} (input {error.get('input')!r})"


def _bind(
    env: Environment, cls: type[BaseModel], prefix: str, bean_name: str
) -> BaseModel:
    """Validate the nested dict under ``prefix`` into ``cls``."""
    try:
        return cls.model_validate(collect_prefixed(env, prefix))
    except ValidationError as exc:
        details = "; ".join(_format_error(e) for e in exc.errors())
        raise BeanCreationError(
            f"Error creating bean '{bean_name}' ({cls.__name__}):"
            f" failed to bind properties under '{prefix}': {details}"
        ) from exc


def _is_secret_annotation(annotation: Any) -> bool:
    """True when the annotation is (or contains) ``SecretStr``."""
    if isinstance(annotation, type) and issubclass(annotation, SecretStr):
        return True
    return any(_is_secret_annotation(arg) for arg in get_args(annotation))


def _secret_fields(cls: type[BaseModel]) -> frozenset[str]:
    """Flag fields annotated ``SecretStr`` or ``Field(..., secret=True)``.

    Flagging only — masking itself is the actuator's job (M2).
    """
    secret: set[str] = set()
    for field_name, info in cls.model_fields.items():
        if _is_secret_annotation(info.annotation):
            secret.add(field_name)
            continue
        extra = info.json_schema_extra
        if isinstance(extra, dict) and extra.get("secret") is True:
            secret.add(field_name)
    return frozenset(secret)


@dataclass(frozen=True)
class ConfigPropertiesMeta:
    """Marker attached to ``@ConfigurationProperties`` classes.

    ``build_definition`` is the extension point used by
    ``ApplicationContext.register``; the returned definition is a normal
    singleton def, so conditions, AOP and post-processors all apply.
    ``secret_fields`` records secret-flagged field names (masking is M2).
    """

    prefix: str
    name: str | None
    secret_fields: frozenset[str]

    def build_definition(
        self, ctx: ApplicationContext, cls: type[BaseModel]
    ) -> BeanDefinition:
        bean_name = self.name or decapitalize(cls.__name__)
        prefix = self.prefix

        def factory() -> BaseModel:
            return _bind(ctx.environment, cls, prefix, bean_name)

        return BeanDefinition(
            name=bean_name,
            bean_type=cls,
            factory=factory,
            scope=Scope.SINGLETON,
            # Props beans bind eagerly at refresh() — like Spring Boot —
            # so misconfiguration fails fast instead of on first access.
            lazy=False,
            conditions=conditions_of(cls),
        )


def ConfigurationProperties(
    *,
    prefix: str,
    name: str | None = None,
) -> Callable[[type[T]], type[T]]:
    """Mark a pydantic ``BaseModel`` as bindable from environment properties.

    ``ctx.register(cls)`` creates a singleton bean (named ``name`` or the
    decapitalized class name) whose factory flattens the env keys under
    ``prefix.`` into a nested dict and runs ``cls.model_validate`` on it.
    Unknown keys inside the prefix are ignored; model defaults fill unset
    fields; nested models bind naturally. Validation failure raises
    ``BeanCreationError`` naming the bean, the key path and the bad value.
    """
    if not prefix:
        raise PyleafError("@ConfigurationProperties requires a non-empty prefix")

    def wrap(cls: type[T]) -> type[T]:
        if not isinstance(cls, type) or not issubclass(cls, BaseModel):
            raise PyleafError(
                "@ConfigurationProperties target must be a pydantic"
                f" BaseModel subclass, got {getattr(cls, '__name__', cls)}"
            )
        setattr(
            cls,
            CONFIG_PROPS_ATTR,
            ConfigPropertiesMeta(
                prefix=prefix,
                name=name,
                secret_fields=_secret_fields(cls),
            ),
        )
        return cls

    return wrap
