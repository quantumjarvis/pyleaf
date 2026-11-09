"""Method-security auto-configuration: the interceptor aspect bean.

Registered through the ``pyleaf.autoconfigure`` entry point. pyleaf-
security depends only on pyleaf-core, so ``SecurityAutoConfiguration``
uses core's ``@Configuration`` — equivalent to ``@AutoConfiguration``
for registration purposes; the ``__pyleaf_autoconfigure__`` marker is
set for parity with boot's ordering/discovery conventions (same shape
as ``TxAutoConfiguration``).

One bean: ``methodSecurityInterceptor``, the ``@Aspect`` that joins
marked methods to the security checks. It is registered unconditionally
— pyleaf-security present = method security on — and is a no-op when
nothing is annotated. The ``pyleaf.security.order`` property tunes the
advice's ``@Order`` (default 50: OUTSIDE the tx interceptor's 100, so
denied calls never open a transaction).
"""

from __future__ import annotations

from pyleaf.core import ApplicationContext, Bean, Configuration
from pyleaf.core.stereotypes import ORDER_ATTR

from .method import (
    DEFAULT_SECURITY_ORDER,
    SECURITY_ORDER_PROPERTY,
    MethodSecurityInterceptor,
)


@Configuration
class SecurityAutoConfiguration:
    """Provides the method-security interceptor aspect bean."""

    # Parity with @AutoConfiguration-marked classes for EP discovery.
    __pyleaf_autoconfigure__ = True

    @Bean
    def methodSecurityInterceptor(
        self, ctx: ApplicationContext
    ) -> MethodSecurityInterceptor:
        """Create the interceptor; apply ``pyleaf.security.order``."""
        order = ctx.environment.get(
            SECURITY_ORDER_PROPERTY, int, DEFAULT_SECURITY_ORDER
        )
        if isinstance(order, int):
            setattr(MethodSecurityInterceptor, ORDER_ATTR, order)
        return MethodSecurityInterceptor()


__all__ = ["SecurityAutoConfiguration"]
