# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/OWNER/REPO/releases/tag/v0.1.0
