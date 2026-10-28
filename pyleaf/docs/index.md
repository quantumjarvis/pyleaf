# pyleaf

A Spring-inspired dependency-injection and auto-configuration framework for
Python.

pyleaf provides the machinery that makes Spring applications inspectable —
conditional bean registration with a human-readable
`ConditionEvaluationReport`, starter-based auto-configuration discovered
through package entry points, and a shared method-interceptor chain that
transactions, caching, security, and retry will all compose on.

!!! note "Status"
    **v0.1.0 — M1 (foundation) complete.** The container, AOP, events, and
    boot layers shipped. Web, transactions, security, and caching are
    planned milestones; see the feature chart in the repository README.

## Packages

| Distribution | Import | Purpose |
|---|---|---|
| `pyleaf-core` | `pyleaf.core` | DI container, stereotypes, lifecycle, conditions + report, AOP interceptors, `Environment`, events |
| `pyleaf-boot` | `pyleaf.boot` | `PyleafApplication.run()`, auto-configuration ordering/discovery, `application.yaml` loading, `@ConfigurationProperties`, graceful shutdown |
| `pyleaf-starter-sqlalchemy` | `pyleaf.starter.sqlalchemy` | Reference starter: conditional `Engine` + `sessionmaker` beans |
| `pyleaf-test` | `pyleaf.test` | pytest plugin: `@PyleafTest`, `pyleaf_context` fixture, `mock_bean`/`spy_bean` |

## Design pillars

- **Why does this bean exist?** Conditions (`@ConditionalOnClass`,
  `@ConditionalOnMissingBean`, …) plus a condition report that lists every
  matched and unmatched definition with reasons.
- **One interceptor chain.** `MethodInterceptor` beans apply via composable
  pointcuts, ordered by `@Order` — the foundation M3 transactions and M4
  caching build on.
- **Strict typing.** `mypy --strict` clean; `ctx.get(Foo)` returns a `Foo`.
- **Self-invocation is intercepted.** Methods are wrapped on the instance,
  so `self.internal()` calls go through the chain — a deliberate departure
  from Spring's proxy model.

## Next steps

- [Getting started](getting-started.md) — install, run the example, write
  your first app.
- [pyleaf-core](packages/core.md) — the container API in detail.
- [pyleaf-boot](packages/boot.md) — auto-configuration and configuration
  properties.
- [pyleaf-test](packages/test.md) — testing your application.
