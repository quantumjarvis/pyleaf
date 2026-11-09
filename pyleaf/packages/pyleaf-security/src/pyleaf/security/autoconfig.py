"""Security auto-configuration: method interceptor + web beans (3.7/3.8).

Registered through the ``pyleaf.autoconfigure`` entry point. pyleaf-
security depends only on pyleaf-core, so ``SecurityAutoConfiguration``
uses core's ``@Configuration`` — equivalent to ``@AutoConfiguration``
for registration purposes; the ``__pyleaf_autoconfigure__`` marker is
set for parity with boot's ordering/discovery conventions (same shape
as ``TxAutoConfiguration``).

Beans:

- ``methodSecurityInterceptor`` — the ``@Aspect`` joining marked methods
  to the security checks, registered unconditionally (a no-op when
  nothing is annotated). ``pyleaf.security.order`` tunes the advice's
  ``@Order`` (default 50: OUTSIDE the tx interceptor's 100, so denied
  calls never open a transaction).
- ``securityMiddleware`` — only when ``pyleaf.web`` is importable
  (``@ConditionalOnClass`` on ``pyleaf.web.WebMiddleware``: the parent
  module probe is what actually gates). Reads ``pyleaf.security.rules``,
  ``pyleaf.security.default-access`` and
  ``pyleaf.security.actuator.enabled`` — see ``pyleaf.security.web``.
- ``jwtAuthenticationProvider`` — when pyjwt is installed AND a key
  property is set (``pyleaf.security.jwt.secret`` -> HS256 or
  ``pyleaf.security.jwt.public-key`` -> RS256); backs off to a user bean.
- ``passwordEncoder`` — a ``DelegatingPasswordEncoder`` when argon2-cffi
  or bcrypt is installed (argon2 preferred default); backs off to a
  user ``PasswordEncoder`` bean. ``{noop}`` decoding is NOT enabled.

``@ConditionalOnClass``/custom conditions repeat on every ``@Bean``
method — core does not propagate class-level conditions to bean-method
definitions. The jwt/web/password modules stay importable without their
optional dependencies (guarded imports, lazy ``import`` in call sites),
so the annotations always resolve and the OnClass gate drops the def.
"""

from __future__ import annotations

from typing import ClassVar

from pyleaf.core import (
    ApplicationContext,
    Bean,
    Conditional,
    ConditionalOnClass,
    ConditionalOnMissingBean,
    ConditionContext,
    ConditionResult,
    Configuration,
    Environment,
)
from pyleaf.core.errors import PyleafError
from pyleaf.core.stereotypes import ORDER_ATTR

from .jwt import (
    JWT_AUDIENCE_PROPERTY,
    JWT_ISSUER_PROPERTY,
    JWT_PUBLIC_KEY_PROPERTY,
    JWT_SECRET_PROPERTY,
    JwtAuthenticationProvider,
)
from .method import (
    DEFAULT_SECURITY_ORDER,
    SECURITY_ORDER_PROPERTY,
    MethodSecurityInterceptor,
)
from .password import (
    DelegatingPasswordEncoder,
    PasswordEncoder,
    build_default_encoder,
)
from .web import SecurityMiddleware


class _JwtConfigured:
    """Match when a JWT key property (``secret`` or ``public-key``) is set."""

    bean_presence: ClassVar[bool] = False

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        secret = ctx.environment.get(JWT_SECRET_PROPERTY)
        public_key = ctx.environment.get(JWT_PUBLIC_KEY_PROPERTY)
        matched = secret is not None or public_key is not None
        verdict = (
            f"'{JWT_SECRET_PROPERTY}' or '{JWT_PUBLIC_KEY_PROPERTY}' is set"
            if matched
            else f"neither '{JWT_SECRET_PROPERTY}' nor"
            f" '{JWT_PUBLIC_KEY_PROPERTY}' is set"
        )
        return ConditionResult(matched, f"jwt key property: {verdict}")


class _PasswordHasherAvailable:
    """Match when ``argon2`` (argon2-cffi) or ``bcrypt`` is importable."""

    bean_presence: ClassVar[bool] = False

    def matches(self, ctx: ConditionContext) -> ConditionResult:
        matched = ctx.is_importable("argon2") or ctx.is_importable("bcrypt")
        verdict = (
            "a hasher library is importable"
            if matched
            else "neither 'argon2' nor 'bcrypt' is importable"
        )
        return ConditionResult(matched, f"password hasher: {verdict}")


@Configuration
class SecurityAutoConfiguration:
    """Provides the method interceptor and the optional web beans."""

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

    @Bean
    @ConditionalOnClass("pyleaf.web.WebMiddleware")
    def securityMiddleware(self, ctx: ApplicationContext) -> SecurityMiddleware:
        """The ``@WebMiddleware`` bean; only when pyleaf-web is installed."""
        return SecurityMiddleware(ctx)

    @Bean
    @ConditionalOnClass("jwt")
    @Conditional(_JwtConfigured)
    @ConditionalOnMissingBean(JwtAuthenticationProvider)
    def jwtAuthenticationProvider(
        self, environment: Environment
    ) -> JwtAuthenticationProvider:
        """HS256 (``secret``) or RS256 (``public-key``) JWT provider."""
        secret = environment.get(JWT_SECRET_PROPERTY)
        public_key = environment.get(JWT_PUBLIC_KEY_PROPERTY)
        issuer = environment.get(JWT_ISSUER_PROPERTY)
        audience = environment.get(JWT_AUDIENCE_PROPERTY)
        issuer_s = None if issuer is None else str(issuer)
        audience_s = None if audience is None else str(audience)
        if secret is not None:
            return JwtAuthenticationProvider(
                secret=str(secret), issuer=issuer_s, audience=audience_s
            )
        if public_key is not None:
            return JwtAuthenticationProvider(
                public_key=str(public_key),
                issuer=issuer_s,
                audience=audience_s,
            )
        # Unreachable: _JwtConfigured gates this bean on a key property.
        raise PyleafError(  # pragma: no cover
            "jwtAuthenticationProvider requires "
            f"'{JWT_SECRET_PROPERTY}' or '{JWT_PUBLIC_KEY_PROPERTY}'"
        )

    @Bean
    @Conditional(_PasswordHasherAvailable)
    @ConditionalOnMissingBean(PasswordEncoder)
    def passwordEncoder(self) -> DelegatingPasswordEncoder:
        """Default ``DelegatingPasswordEncoder`` (argon2 preferred)."""
        return build_default_encoder()


__all__ = ["SecurityAutoConfiguration"]
