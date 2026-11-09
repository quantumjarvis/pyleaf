"""Transaction auto-configuration: the ``@Transactional`` interceptor bean.

Registered through the ``pyleaf.autoconfigure`` entry point. pyleaf-tx
depends only on pyleaf-core, so ``TxAutoConfiguration`` uses core's
``@Configuration`` — equivalent to ``@AutoConfiguration`` for
registration purposes (``register_autoconfigs`` registers whatever class
the entry point resolves to); the ``__pyleaf_autoconfigure__`` marker is
set for parity with boot's ordering/discovery conventions.

One bean: ``transactionInterceptor``, the ``@Aspect`` that joins
``@Transactional`` methods to the transaction state machine. It is
registered unconditionally — the aspect is harmless when nothing is
annotated, and manager resolution is lazy: a ``@Transactional`` method
with no resolvable manager fails at first invocation with a
``TransactionError`` naming the method (see
:mod:`pyleaf.tx.interceptor`).

The ``pyleaf.tx.order`` property tunes the advice's ``@Order`` (default
100 — outermost, per Spring's tx convention).
"""

from __future__ import annotations

from pyleaf.core import ApplicationContext, Bean, Configuration
from pyleaf.core.stereotypes import ORDER_ATTR

from .events import TxEventListenerPostProcessor
from .interceptor import TX_ORDER_PROPERTY, TransactionInterceptor


@Configuration
class TxAutoConfiguration:
    """Provides the ``@Transactional`` interceptor aspect bean."""

    # Parity with @AutoConfiguration-marked classes for EP discovery.
    __pyleaf_autoconfigure__ = True

    @Bean
    def transactionInterceptor(
        self, ctx: ApplicationContext
    ) -> TransactionInterceptor:
        """Create the interceptor; apply the ``pyleaf.tx.order`` property."""
        order = ctx.environment.get(TX_ORDER_PROPERTY, int, 100)
        if isinstance(order, int):
            setattr(TransactionInterceptor, ORDER_ATTR, order)
        return TransactionInterceptor(ctx)

    @Bean
    def txEventListenerPostProcessor(self) -> TxEventListenerPostProcessor:
        """The BPP wiring ``@TransactionalEventListener`` deferral.

        Registered unconditionally like the interceptor: with no marked
        listener methods it is a no-op.
        """
        return TxEventListenerPostProcessor()


__all__ = ["TxAutoConfiguration"]
