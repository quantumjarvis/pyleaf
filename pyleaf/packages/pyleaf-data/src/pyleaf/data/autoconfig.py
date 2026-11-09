"""Data-layer auto-configuration (WI 3.6, PYL-37).

Registered through the ``pyleaf.autoconfigure`` entry point as
``data = pyleaf.data.autoconfig:DataAutoConfiguration``. Owns two pieces:

* the ``alembicMigrationRunner`` bean — mixed in from
  :class:`~pyleaf.data.migrations.MigrateOnStartBeans` so the whole
  migrate-on-start wiring lives in :mod:`pyleaf.data.migrations`;
* ``@EnableTransactionManagement`` + the detectable marker bean below.

``@EnableTransactionManagement`` — honest semantics
---------------------------------------------------
In pyleaf, transaction support is **on by default** whenever a
``TransactionManager`` bean exists: the ``tx`` entry point from
pyleaf-tx registers the ``@Transactional`` interceptor unconditionally
and manager resolution is lazy (by protocol type, or ``manager="name"``).
There is nothing to switch on — exactly like Spring, where
spring-boot-autoconfigure also enables the tx interceptor implicitly and
``@EnableTransactionManagement`` is only needed in a bare spring-tx
context. This decorator exists so applications can *declare* the intent:
it stamps :data:`ENABLE_TX_ATTR` on the configuration class and causes
this auto-configuration to register the ``transactionManagementMarker``
bean — both detectable from ``ctx``/the condition report.
"""

from __future__ import annotations

from typing import ClassVar, TypeVar

from pyleaf.boot import AutoConfiguration
from pyleaf.core import (
    Bean,
    Conditional,
    ConditionalOnClass,
    ConditionContext,
    ConditionResult,
)

from .migrations import MigrateOnStartBeans

T = TypeVar("T")

#: Attribute ``@EnableTransactionManagement`` stamps on the configuration.
ENABLE_TX_ATTR = "__pyleaf_enable_transaction_management__"


def EnableTransactionManagement(target: type[T]) -> type[T]:
    """Explicit opt-in marker for transaction management.

    Stamps a detectable flag on the ``@Configuration`` class and lets
    ``DataAutoConfiguration`` register the
    ``transactionManagementMarker`` bean. Transaction interception itself
    needs no switch — the ``tx`` auto-configuration already provides the
    interceptor whenever a ``TransactionManager`` bean exists — so this
    is intentionally a no-op-friendly declaration, documented honestly
    (Spring's annotation is likewise only strictly needed without
    spring-boot autoconfigure).
    """
    setattr(target, ENABLE_TX_ATTR, True)
    return target


class TransactionManagementMarker:
    """Bean proving an app declared ``@EnableTransactionManagement``."""


class OnEnableTransactionManagementCondition:
    """Match when any registered configuration carries the marker flag."""

    bean_presence: ClassVar[bool] = False

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        found = [
            defn.name
            for defn in ctx.registry.all()
            if defn.name != ctx.excluding
            and getattr(defn.bean_type, ENABLE_TX_ATTR, False)
        ]
        names = ", ".join(found) if found else "none"
        return ConditionResult(
            bool(found),
            f"@EnableTransactionManagement found configurations: {names}",
        )


@AutoConfiguration
@ConditionalOnClass("sqlalchemy")
class DataAutoConfiguration(MigrateOnStartBeans):
    """Data-layer auto-configuration: tx opt-in marker + migrations.

    Class-level ``@ConditionalOnClass`` drops the configuration when
    sqlalchemy is absent; every ``@Bean`` method repeats its own gates —
    core does not propagate class-level conditions to bean-method defs
    (see the starter's autoconfigure module docstring).
    """

    @Bean
    @ConditionalOnClass("sqlalchemy")
    @Conditional(OnEnableTransactionManagementCondition)
    def transactionManagementMarker(self) -> TransactionManagementMarker:
        """Register the detectable record of the tx opt-in declaration."""
        return TransactionManagementMarker()


__all__ = [
    "ENABLE_TX_ATTR",
    "DataAutoConfiguration",
    "EnableTransactionManagement",
    "OnEnableTransactionManagementCondition",
    "TransactionManagementMarker",
]
