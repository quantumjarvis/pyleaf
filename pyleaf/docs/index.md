# pyleaf

A Spring-inspired dependency-injection and auto-configuration framework for
Python.

pyleaf provides the machinery that makes Spring applications inspectable —
conditional bean registration with a human-readable
`ConditionEvaluationReport`, starter-based auto-configuration discovered
through package entry points, and a shared method-interceptor chain that
transactions, caching, security, and retry will all compose on.

!!! note "Status"
    **M2 (web) complete — v0.2.0 in progress.** M1 shipped the container,
    AOP, events, and boot layers; M2 adds `@RestController` apps over
    FastAPI, the REQUEST bean scope, `@ControllerAdvice`/`@WebMiddleware`,
    uvicorn serving from `run()`, actuator endpoints, and the
    `pyleaf_client` test fixture. Transactions, security, and caching are
    planned milestones; see the feature chart in the repository README.

## Packages

| Distribution | Import | Purpose |
|---|---|---|
| `pyleaf-core` | `pyleaf.core` | DI container, stereotypes, lifecycle, conditions + report, AOP interceptors, `Environment`, events, REQUEST scope |
| `pyleaf-boot` | `pyleaf.boot` | `PyleafApplication.run()`, auto-configuration ordering/discovery, `application.yaml` loading, `@ConfigurationProperties`, graceful shutdown, uvicorn server mode |
| `pyleaf-starter-sqlalchemy` | `pyleaf.starter.sqlalchemy` | Reference starter: conditional `Engine` + `sessionmaker` beans |
| `pyleaf-test` | `pyleaf.test` | pytest plugin: `@PyleafTest`, `pyleaf_context` + `pyleaf_client` fixtures, `mock_bean`/`spy_bean` |
| `pyleaf-web` | `pyleaf.web` | `@RestController` + `@*Mapping` over FastAPI, `@ControllerAdvice`, `@WebMiddleware`, `WebAutoConfiguration` |
| `pyleaf-actuator` | `pyleaf.actuator` | `/actuator/{health,info,env,beans,conditions,metrics}` with secret masking |

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
