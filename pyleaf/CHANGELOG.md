# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased] — v0.2.0 draft

Milestone 2 — web. `pyleaf-web` (Spring-style class controllers over
FastAPI, REQUEST scope, controller advice, bean middleware,
`WebAutoConfiguration` + uvicorn serving), `pyleaf-actuator`
(operational endpoints with masking), the `pyleaf_client` test fixture,
and the `examples/todo-api` CRUD app. The `v0.2.0` tag is cut after the
M2 quality tasks land; until then this section is a draft.

### Added

- **@RestController + HTTP mappings** (WI 2.1): `pyleaf-web` — class
  controllers over FastAPI: `@RestController(prefix)` as a component
  stereotype; `GetMapping`/`PostMapping`/`PutMapping`/`PatchMapping`/
  `DeleteMapping`/`RequestMapping` method decorators (`status_code`,
  `response_model`, `add_api_route` kwargs, stackable); `build_router` /
  `register_controllers` mounting bound methods in deterministic
  `(@Order`, registration) order with FastAPI parameter/body binding and
  response-model inference.
- **REQUEST scope** (WI 2.2): `Scope.REQUEST` + `RequestScope`
  (contextvar-bound per-request cache, `get_or_create`, `@PreDestroy`
  teardown, `ScopeNotActive` outside a scope), `RequestScopedProxy`
  injected for every request-scoped dependency (one shared proxy per
  definition; `isinstance(proxy, T)` deliberately unsupported), and
  `RequestScopeMiddleware` in pyleaf-web activating a scope per HTTP
  request.
- **@ControllerAdvice + @WebMiddleware** (WI 2.3): `@ControllerAdvice`
  beans with `@ExceptionHandler(*types, status_code=)` methods — one ASGI
  dispatcher middleware resolving the MRO-closest handler (ties by bean
  `@Order`, then declaration), `Request`/exception parameter injection,
  sync or async, `Response` verbatim or `JSONResponse`; FastAPI's own
  validation/`HTTPException` handling passes through untouched.
  `@WebMiddleware` beans with `async dispatch(request, call_next)`,
  mounted in `@Order`-descending order so the lowest `@Order` is
  outermost.
- **WebAutoConfiguration + server mode** (WI 2.4): `WebAutoConfiguration`
  entry point providing `fastapiApp` (controllers + advice + bean
  middleware + `RequestScopeMiddleware` + CORS from `pyleaf.web.cors.*`
  + `/health` readiness + `FastApiCustomizer` extension point) and
  `httpClient` (shared `httpx.AsyncClient` with async-safe close);
  `PyleafApplication.run()` serves the `FastAPI` bean via uvicorn on
  `pyleaf.web.host`/`port`/`workers`/`access-log`, blocking until
  shutdown — the app lifespan is the single `ctx.close()` owner;
  `--no-server`, `no_server=True` or `pyleaf.web.enabled=false` suppress
  serving.
- **pyleaf-actuator** (WI 2.5): `ActuatorAutoConfiguration` mounting
  `/actuator/{health,info,env,beans,conditions,metrics}` through the
  `FastApiCustomizer` extension point; `HealthIndicator` aggregation
  (any `DOWN` -> 503); `/env` masking of `password|secret|token|key`
  keys plus `@ConfigurationProperties` `secret_fields`; `/beans` and
  `/conditions` read definitions only — lazy and request-scoped beans
  are never instantiated; per-endpoint `enabled` flags,
  `pyleaf.actuator.base-path`, optional `prometheus` extra.
- **pyleaf_client fixture** (WI 2.6): `pyleaf-test` fixture building the
  same app wiring as `fastapiApp` (controllers + advice + middleware +
  request scope) over the current `pyleaf_context` — `mock_bean`/
  `override_bean` reflected in served responses.
- **todo-api example + M2 e2e + docs** (WI 2.7): `examples/todo-api`
  CRUD app exercising the whole M2 stack (`@RestController` +
  service/repository over the SQLAlchemy starter, `@ControllerAdvice`
  404, `@WebMiddleware` request-id header, a REQUEST-scoped audit bean);
  `scripts/e2e_m2.sh` fresh-venv real-uvicorn HTTP contract (health,
  CRUD round-trip, `/actuator/beans` definitions-only, `/env` masking,
  middleware header, openapi paths); `docs/web.md`, `docs/actuator.md`.

### Changed

- **Internal beans match only their exact type** (WI 2.4): `applicationContext`,
  `environment` and `eventPublisher` are registered `internal=True` and
  no longer appear in broader type lookups — `ctx.get_all(object)`
  (e.g. runner discovery) excludes them.
- **Default web host is `127.0.0.1`** (WI 2.4): deliberately not Spring's
  `0.0.0.0` — a first `run()` binds loopback only; set `pyleaf.web.host`
  to expose wider.

## [0.1.0] - 2026-10-27

Milestone 1 — foundation. `pyleaf-core` (typed DI container, conditions +
report, container-applied AOP, events), `pyleaf-boot` (auto-configuration,
properties, application runner), the `pyleaf-test` pytest plugin, and the
`pyleaf-starter-sqlalchemy` reference starter.

### Added

- **Workspace** (WI 1.1): uv workspace of PEP 420 namespace packages;
  shared ruff/mypy-strict/pytest configuration; `AGENTS.md` verify
  commands.
- **DI resolver** (WI 1.2): constructor injection by type hint — concrete
  types, ABC/Protocol bases, `list[T]` ordered by `@Order`, `T | None`,
  `Annotated[T, Qualifier]`; `@Component/@Service/@Repository/@Controller`,
  `@Configuration` + `@Bean`, `@Primary`, `@Lazy`; singleton + prototype
  scopes; startup cycle detection with the full path; `NoSuchBean` /
  `NoUniqueBean` / `CircularDependency` errors.
- **Scanning, lifecycle, post-processors** (WI 1.3): recursive package
  scan to bean definitions; `@PostConstruct` after injection and
  post-processing; `@PreDestroy` on `close()` in reverse creation order
  with isolated failures; `BeanPostProcessor` before/after-init hooks that
  may replace the instance.
- **AOP** (WI 1.4): `MethodInterceptor` / `Invocation` ordered chain;
  pointcut DSL (`within`, `annotated`, `name_matches`, `bean_type`, `&`
  `|` `~`); `@Aspect` with `@Around/@Before/@After/@AfterReturning/
  @AfterThrowing`; instance-level method wrapping via `wrapt` —
  `isinstance` and signatures preserved, `self.method()` self-invocations
  intercepted; dunders/properties/classmethods never wrapped; zero
  overhead for unmatched beans; sync + async methods.
- **Environment, conditions, report** (WI 1.5): layered `PropertySource`s
  with profiles and typed `Environment.get`; `@ConditionalOnClass/
  OnMissingClass/OnBean/OnMissingBean/OnProperty/OnProfile`, custom
  `Condition`s, `@Profile`; two-phase registration so `OnMissingBean`
  sees all unconditional definitions; `ConditionEvaluationReport` with
  reasons for every matched/unmatched definition.
- **Events** (WI 1.6): `@EventListener` bean methods dispatched by
  declared event type (base classes included, plain-object events);
  injectable `EventPublisher` with sync `publish` and `publish_async`
  awaiting async listeners.
- **Configuration & properties** (WI 1.7): `application.yaml` →
  `application-{profile}.yaml` → env vars → `--key=value` CLI precedence;
  `@ConfigurationProperties(prefix)` binding environment keys into
  pydantic models registered as beans, with key-path validation errors.
- **Auto-configuration, run, shutdown** (WI 1.8): `@AutoConfiguration`
  with `@AutoConfigureAfter/Before/Order`; discovery via the
  `pyleaf.autoconfigure` entry-point group, registered after user beans so
  user definitions win `OnMissingBean`; `pyleaf.autoconfigure.exclude`;
  `PyleafApplication.run()` with banner, startup timing, and
  `ApplicationRunner`/`CommandLineRunner`; SIGINT/SIGTERM graceful
  shutdown with timeout; default logging auto-config.
- **Starter + example + E2E** (WI 1.9): `pyleaf-starter-sqlalchemy`
  yielding `Engine` and `sessionmaker` beans from `app.datasource.*`,
  backing off to a user `Engine`, skipped cleanly without sqlalchemy;
  `examples/hello` zero-wiring demo including a counting `@Aspect`
  proving self-invocation interception; `scripts/e2e_m1.sh` fresh-venv
  contract covering all starter behaviours.
- **pyleaf-test** (WI 1.10): pytest plugin — `@PyleafTest(scan=,
  profiles=, properties=, config_dir=)`, the `pyleaf_context` fixture, and
  `mock_bean(T)` / `spy_bean(T)` overrides applied before refresh.
- **Quality & docs** (WI 1.11 + 1.Q1–1.Q3): `benchmarks/m1` performance
  suite with per-benchmark thresholds and a committed baseline;
  `scripts/quality_m1.sh` (pip-audit, bandit, ruff, per-file coverage
  gates ≥ 90% on resolver/aop/conditions/autoconfigure, optional mutmut);
  GitHub Actions CI; README with the full Spring→Python→pyleaf chart;
  mkdocs-material docs skeleton; this changelog.

[0.1.0]: https://github.com/quantumjarvis/pyleaf/releases/tag/v0.1.0
