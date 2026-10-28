# pyleaf

A Spring-inspired dependency-injection and auto-configuration framework for
Python — a typed DI container with conditions and a condition report,
container-applied AOP, and Boot-style auto-configuration delivered as
installable starters.

> **Status: v0.1.0 — M1 (foundation) complete.**
>
> Shipped in M1: DI container (`pyleaf-core`), conditions +
> `ConditionEvaluationReport`, container-applied AOP, application events,
> Boot auto-configuration + `PyleafApplication.run()`, the starter model
> (`pyleaf-starter-sqlalchemy` as reference), and the `pyleaf-test` pytest
> plugin. Web, transactions/data, security, caching/clustering, and
> scheduling are planned milestones (see the chart below).

[![ci](https://github.com/quantumjarvis/pyleaf/actions/workflows/ci.yml/badge.svg)](https://github.com/quantumjarvis/pyleaf/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pyleaf-core.svg)](https://pypi.org/project/pyleaf-core/)

## Spring → Python → pyleaf: the feature chart

Legend: **Reuse** = wrap an existing library. **Glue** = thin adapter.
**Build** = written in pyleaf. Value = community benefit H/M/L.

### 1. Core container (`pyleaf-core`) — M1 ✅

| Spring | Python today | pyleaf | Value |
|---|---|---|---|
| `@Component/@Service/@Repository/@Controller` | DI libs need explicit registration | Build | M |
| Constructor injection by type hint | dishka/injector/dependency-injector | Build (own engine) | — |
| `@Autowired` field injection | un-Pythonic | Skip; constructor-only | L |
| `@Qualifier`, `@Primary` | `Annotated[T, "name"]` pattern | Build | M |
| Scopes singleton/prototype/request | dishka scope hierarchy | Build singleton+prototype; request scope via contextvars in `web` | M |
| `@PostConstruct/@PreDestroy` | svcs/dishka finalizers | Build | M |
| `@ComponentScan` | appctx, injector-autowired | Build (pkgutil walk) | M |
| `@Lazy` | dishka lazy default | Build (lazy by default, eager opt-in) | L |
| Circular dependency detection | appctx, dishka | Build (startup-time graph check) | M |
| `@Profile` | injector-autowired, dynaconf envs | Build | M |
| **`@ConditionalOnClass/OnMissingBean/OnBean/OnProperty/OnProfile/OnMissingClass`** | **nothing** | **Build — key gap** | **H** |
| `ApplicationEvent/@EventListener` | blinker | Build thin, sync+async | M |
| **`BeanPostProcessor` / `MethodInterceptor` chain** | wrapt (proxies), no container-level hook anywhere | **Build** — foundation for tx/cache/security/retry | **M-H** |
| `Environment` / placeholders | pydantic-settings, dynaconf | Build `Environment` abstraction over layered sources | M |

### 1b. AOP (`pyleaf.core.aop`) — M1 ✅

| Spring AOP | Python today | pyleaf | Value |
|---|---|---|---|
| `@Aspect` with `@Around/@Before/@After/@AfterReturning/@AfterThrowing` | plain decorators; `aspectlib` (stale) | Build; `@Around` is the primitive, others sugar | M |
| Pointcut expressions (AspectJ strings) | none | Build small composable DSL: `within("pkg.service")`, `annotated(Marker)`, `name_matches("find_*")`, `bean_type(T)`, `&` `\|` `~`. **No** AspectJ string parser | M |
| `MethodInterceptor`, ordered chain, interceptors are beans (get DI) | none | Build; `@Order` on aspects/interceptors | M-H |
| JDK/CGLIB proxies | `wrapt` | **Wrap bound methods on the real instance** via `wrapt.FunctionWrapper`; `isinstance` holds, no subclassing, no `final` issue, signatures preserved for mypy/FastAPI | — |
| Self-invocation gotcha | — | **Not present**: because methods are wrapped on the instance, `self.method()` is intercepted. Documented as a deliberate difference from Spring | M |
| Async methods | — | Interceptor chain detects coroutine functions; `@Around` aspects may be sync or async | H |
| Introductions, field pointcuts, compile-time weaving | — | Skip | L |

### 2. Boot (`pyleaf-boot`) — M1 ✅

| Spring Boot | Python today | pyleaf | Value |
|---|---|---|---|
| Auto-configuration classes | nothing conditional | **Build** `@AutoConfiguration`, `@AutoConfigureAfter/Before`, ordering | **H** |
| Starters | `importlib.metadata.entry_points` exists | **Build** convention: entry-point group `pyleaf.autoconfigure` | **H** |
| `application.yaml` + profiles + env overrides | pydantic-settings, dynaconf, PyYAML | **Reuse pydantic-settings + PyYAML**; profile-specific `application-{profile}.yaml` | M |
| `@ConfigurationProperties(prefix=)` | `BaseSettings(env_prefix=)` | Glue: decorator -> pydantic model bound from Environment, registered as bean | M |
| `@Value("${x}")` | none | Skip | L |
| `ConditionEvaluationReport` | nothing | **Build** (ships with conditions) | M |
| `SpringApplication.run()` | trivial | Build `PyleafApplication.run()`, banner, startup timing, `ApplicationRunner` | L |
| Graceful shutdown | uvicorn signals | Build SIGTERM -> `PreDestroy` ordering | M |
| DevTools | `uvicorn --reload`, watchfiles | Reuse | — |

### 3. Web (`pyleaf-web`) — M2, planned

| Spring | Python today | pyleaf | Value |
|---|---|---|---|
| `@RestController`, `@GetMapping`… | FastAPI (functions), Litestar/BlackSheep (classes) | Glue class controllers over **FastAPI** router; beans injected into controllers | M |
| Request binding/validation | FastAPI + Pydantic | Reuse | — |
| `@ControllerAdvice/@ExceptionHandler` | FastAPI exception handlers | Glue | L |
| Interceptors / filters | Starlette middleware | Reuse; bean-aware middleware registration | L |
| `RestTemplate/WebClient` | httpx | Reuse; auto-config an `httpx.AsyncClient` bean | L |
| Declarative HTTP client | lapidary, uplink | Defer | L |
| Actuator `/health /info /metrics /env /beans /conditions` | prometheus libs; **no `/beans` `/conditions`** | Build small actuator on prometheus_client | M-H |
| Templates | Jinja2 | Reuse | — |
| OpenAPI | FastAPI | Reuse | — |

### 4. Data + Transactions (`pyleaf-tx`, `pyleaf-tx-sqlalchemy`, `pyleaf-data`) — M3, planned

| Spring | Python today | pyleaf | Value |
|---|---|---|---|
| `PlatformTransactionManager` | SQLAlchemy `Session.begin()`; Zope `transaction` | **Build** `TransactionManager` protocol; SQLAlchemy **sync + async** implementations first | M-H |
| `@Transactional(propagation=)` REQUIRED / REQUIRES_NEW / NESTED / SUPPORTS / NOT_SUPPORTED / MANDATORY / NEVER | pyspring-model only | **Build** as an M1 interceptor: NESTED = `begin_nested()` savepoint; REQUIRES_NEW = push new session on contextvar stack | **H** |
| Isolation, `read_only`, `timeout` | SQLAlchemy `execution_options` | Glue | L |
| `rollback_for` / `no_rollback_for` | — | Build; default rollback on any `Exception` (no checked-exception split) | M |
| Thread-bound session | `contextvars` (threads **and** asyncio tasks) | Build `TransactionSynchronizationManager` on contextvars | — |
| `TransactionTemplate` | `with session.begin()` | Build `tx.execute(fn)` / `with tx.required():` / `async with` | M |
| `TransactionSynchronization`, `@TransactionalEventListener(AFTER_COMMIT)` | nothing | **Build** — after-commit hooks for email/Kafka/outbox | **H** |
| Multiple datasources `@Transactional("secondary")` | — | Build via qualifier on tx manager | M |
| JTA / XA two-phase | Zope `transaction` | Skip; recommend outbox pattern | L |
| JPA/Hibernate | SQLAlchemy 2.x | Reuse | — |
| `CrudRepository` | sqlrepository, nexusrepo, pyspring-model | Build thin typed generic | M |
| Derived queries `find_by_x_and_y` | pyspring-model, nexusrepo | **Skip** — breaks static typing; `@Query` + typed builders instead | L |
| Flyway/Liquibase | Alembic | Reuse; starter runs migrations on boot | — |
| Redis/Mongo | redis-py, motor/beanie | Starters | — |
| Paging | fastapi-pagination | Reuse | — |

### 5. Security (`pyleaf-security`) — M3, planned

| Spring | Python today | pyleaf | Value |
|---|---|---|---|
| Auth filter chain | FastAPI `Security()`, authlib, pyjwt | Glue over authlib/pyjwt | M |
| `@PreAuthorize("hasRole")` | none at bean level | Build as M1 interceptor | M |
| OAuth2 client/resource server | authlib | Reuse | — |
| Password encoders | argon2-cffi, bcrypt | Reuse | — |
| CORS/CSRF | Starlette | Reuse | — |

### 6. Cross-cutting (`pyleaf-cache`, `pyleaf-cluster-hazelcast`, `pyleaf-scheduling`) — M4, planned

| Spring | Python today | pyleaf | Value |
|---|---|---|---|
| `@Cacheable/@CacheEvict/@CachePut` | cachetools, aiocache | Interceptor + pluggable `CacheManager` bean | M |
| Hazelcast auto-config, embedded member + multicast | `hazelcast-python-client` (official, mature; **client-only — embedded member is Java-only**) | **Reuse client** in `pyleaf-cluster-hazelcast`: client bean from properties, `HazelcastCacheManager`, distributed map/lock/counter/topic wrappers, membership events, session store, cluster-singleton `@Scheduled` | M-H |
| Spring Session (Hazelcast/Redis) | none for FastAPI class apps | Build on top of the cluster map | M |
| `@Scheduled(cron=)` | APScheduler 4 | Glue | M |
| `@Async` | asyncio native | Skip | L |
| `@Retryable` | tenacity, stamina | Interceptor delegating to tenacity | M |
| Validation | Pydantic | Reuse | — |
| Logging | structlog / logging.config | Auto-config starter | L |
| Metrics/tracing | OpenTelemetry, prometheus_client | Starter; tracing interceptor | M |

### 7. Messaging / Cloud / Batch / AI — M5 or skip, planned

| Spring | Python today | pyleaf |
|---|---|---|
| `@KafkaListener/@RabbitListener` | **FastStream**, aiokafka, aio-pika | Starter bridging FastStream brokers as beans |
| Task queues | Celery, taskiq | Starter |
| Spring Batch | no equivalent | Skip |
| Spring Cloud Config/Discovery/Gateway | Kubernetes/consul | Skip |
| Spring Integration | none | Skip |
| Spring AI | pydantic-ai, litellm, LangChain | Optional starter later |
| GraphQL / gRPC | strawberry, grpcio | Starters |

## Quickstart

From the workspace root (`pyleaf/`):

```bash
uv sync                          # resolve and install the workspace
cd examples && uv run python -m hello
```

Expected output (abridged):

```text
pyleaf 0.1.0 — hello
Hello from pyleaf!
ASPECT_INTERNAL_COUNT=2
ENGINE_OK url=sqlite:///%3Amemory%3A
```

`examples/hello` boots with zero wiring code: `PyleafApplication.run("hello")`
loads `application.yaml`, scans the `hello` package, discovers the SQLAlchemy
starter through the `pyleaf.autoconfigure` entry point, and refreshes the
context. `ASPECT_INTERNAL_COUNT=2` proves a self-invocation
(`public() -> self.internal()`) plus a direct `internal()` call are both
intercepted — see "Deliberate differences" below.

A minimal application is a `@Service` plus a `PyleafApplication.run` call:

```python
# myapp/service.py
from pyleaf.core import Service

@Service
class GreetingService:
    def greet(self) -> str:
        return "Hello from pyleaf!"
```

```python
# myapp/__main__.py
from pyleaf.boot import PyleafApplication
from .service import GreetingService

ctx = PyleafApplication.run("myapp")
try:
    print(ctx.get(GreetingService).greet())
finally:
    ctx.close()
```

## Deliberate differences from Spring

- **Self-invocation IS intercepted.** Methods are wrapped on the instance
  (via `wrapt`), not behind a proxy, so `self.method()` calls inside a bean
  go through the interceptor chain. Spring's proxy-based AOP misses these.
- **Constructor injection only.** No field/setter injection, no `@Autowired`.
  Dependencies are type-hinted `__init__` parameters (`list[T]`, `T | None`,
  `Annotated[T, Qualifier("name")]` are all supported).
- **No `@Value("${...}")`.** Configuration is bound to typed
  `@ConfigurationProperties` pydantic models or read through the injected
  `Environment` — no per-field placeholder strings.
- **No AspectJ string pointcuts.** Pointcuts are a small composable Python
  DSL: `within("pkg.service")`, `annotated(Marker)`, `name_matches("find_*")`,
  `bean_type(T)`, combined with `&`, `|`, `~`.
- **Lazy by default.** Singletons are created on first `get()` unless
  `@Lazy(False)` or `refresh(eager=True)`; `PyleafApplication.run` refreshes
  eagerly like Spring Boot.
- **No checked-exception split.** (Planned M3) `@Transactional` rolls back on
  any `Exception` unless `no_rollback_for` says otherwise.

## Repository layout

```
pyleaf/                       # uv workspace root
├── packages/
│   ├── pyleaf-core/          # DI container, AOP, conditions, env, events
│   ├── pyleaf-boot/          # auto-config, properties, PyleafApplication
│   ├── pyleaf-starter-sqlalchemy/   # reference starter
│   └── pyleaf-test/          # pytest plugin
├── examples/hello/           # zero-wiring demo app (see Quickstart)
├── benchmarks/m1/            # performance suite with thresholds
├── scripts/                  # e2e_m1.sh, quality_m1.sh
└── docs/                     # mkdocs-material documentation
```

## Verification

```bash
uv sync
uv run pytest -q                 # unit/integration suite (263 tests)
uv run mypy --strict packages/*/src
uv run ruff check .
uv run pytest benchmarks/m1 -q   # perf suite (opt-in, not in testpaths)
bash scripts/e2e_m1.sh           # fresh-venv end-to-end contract
bash scripts/quality_m1.sh       # pip-audit, bandit, coverage gates
uv run mkdocs build --strict     # docs
```

## Documentation

The `docs/` tree is built with mkdocs-material (`uv run mkdocs serve` for a
live preview, `uv run mkdocs build` for the static site):

- [Getting started](docs/getting-started.md)
- [pyleaf-core](docs/packages/core.md) — container, AOP, conditions, events
- [pyleaf-boot](docs/packages/boot.md) — auto-configuration, properties, run
- [pyleaf-starter-sqlalchemy](docs/packages/starter-sqlalchemy.md)
- [pyleaf-test](docs/packages/test.md) — pytest plugin
- [CHANGELOG](CHANGELOG.md)

## Name and trademark note

The project name is **pyleaf**; the import namespace is `pyleaf.*` and
distributions are named `pyleaf-core`, `pyleaf-boot`, etc. The bare `pyleaf`
name on PyPI is held by a dead 2013 package — a PEP 541 reclaim is planned
for M6; until then there is no `pip install pyleaf` meta-package.

pyleaf is **inspired by** Spring. It is not affiliated with, endorsed by, or
derived from Spring, VMware, or Broadcom. "Spring" and "Spring Boot" are
trademarks of Broadcom Inc.; they are used here only to describe the design
lineage.
