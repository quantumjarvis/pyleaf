"""M3 benchmark: @PreAuthorize check overhead vs an equivalent advised call.

The comparison arm is a bean method advised by a pass-through ``@Around``
aspect — the same wrapper + ``Invocation`` + single-advice dispatch
machinery, minus the check — so the measured ratio isolates what
``MethodSecurityInterceptor`` adds on top of dispatch: security-meta
re-resolution, the ``SecurityContextHolder`` contextvar read, argument
binding and the ~1 us expression eval. A ratio against a completely
unadvised (~0.1 us) method would be dominated by the AOP dispatch cost
already gated in ``benchmarks/m1/test_bench_aop.py``, so — like the m2
middleware gate against a hand-mounted raw stack — what is pinned here
is the overhead the check itself adds.

Measured locally: secured ~2 us vs advised-raw ~0.2 us (~10x); the gate
at 15x leaves ~50% headroom and scales with the hardware since both
arms share the same dispatch path.
"""

from typing import Any

from pyleaf.core import (
    ApplicationContext,
    Around,
    Aspect,
    Component,
    Invocation,
    bean_type,
)
from pyleaf.security import (
    Authentication,
    PreAuthorize,
    Principal,
    SecurityAutoConfiguration,
    SecurityContextHolder,
    role,
)
from webbench import median_per_call

RATIO_LIMIT = 15.0
ABSOLUTE_THRESHOLD_S = 25e-6


@Component
class BenchGuardedService:
    """Bean whose ``work`` carries a @PreAuthorize check."""

    @PreAuthorize("has_role('USER')")
    def work(self) -> str:
        return "done"


@Component
class BenchPlainService:
    """Bean advised by a no-op aspect — the equivalent-stack raw arm."""

    def work(self) -> str:
        return "done"


@Aspect
class PassthroughAspect:
    """Wrap ``BenchPlainService`` methods with a bare ``proceed()``."""

    @Around(bean_type(BenchPlainService))
    def invoke(self, invocation: Invocation) -> Any:
        return invocation.proceed()


def _ctx() -> ApplicationContext:
    ctx = ApplicationContext()
    for cls in (
        BenchGuardedService,
        BenchPlainService,
        PassthroughAspect,
        SecurityAutoConfiguration,
    ):
        ctx.register(cls)
    ctx.refresh()
    return ctx


def test_preauthorize_check_overhead(benchmark) -> None:
    ctx = _ctx()
    try:
        guarded = ctx.get(BenchGuardedService)
        plain = ctx.get(BenchPlainService)
        auth = Authentication(
            principal=Principal("alice"),
            authorities=frozenset({role("USER")}),
            authenticated=True,
        )
        with SecurityContextHolder.with_authentication(auth):
            # Warm the interceptor's signature cache + the advice chain.
            assert guarded.work() == "done"
            assert plain.work() == "done"

            raw_s = median_per_call(plain.work)
            assert benchmark(guarded.work) == "done"
            secured_s = benchmark.stats["median"]

            ratio = secured_s / raw_s
            benchmark.extra_info["raw_median_s"] = raw_s
            benchmark.extra_info["overhead_ratio"] = ratio
            assert ratio < RATIO_LIMIT, (
                f"@PreAuthorize check overhead {ratio:.3f}x exceeds "
                f"{RATIO_LIMIT:.2f}x (secured {secured_s * 1e6:.1f} us "
                f"vs advised-raw {raw_s * 1e6:.1f} us)"
            )
            assert secured_s < ABSOLUTE_THRESHOLD_S, (
                f"secured call {secured_s * 1e6:.1f} us exceeds "
                f"{ABSOLUTE_THRESHOLD_S * 1e6:.0f} us"
            )
    finally:
        ctx.close()
