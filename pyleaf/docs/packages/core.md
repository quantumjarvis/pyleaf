# pyleaf-core

`pyleaf.core` is the foundation: a strictly typed dependency-injection
container with component scanning, lifecycle hooks, conditional
registration, an `Environment` abstraction, application events, and
container-applied AOP. It is usable entirely on its own — no boot layer
required.

```python
from pyleaf.core import ApplicationContext

ctx = ApplicationContext(scan=["myapp"])
ctx.refresh()
service = ctx.get(GreetingService)   # typed as GreetingService
ctx.close()
```

## Stereotypes and configuration

| Decorator | Purpose |
|---|---|
| `@Component`, `@Service`, `@Repository`, `@Controller` | Register a class as a bean (equal semantics, descriptive names) |
| `@Configuration` + `@Bean` | Factory-method bean definitions; params are injected, the return annotation is the bean type |
| `@Primary`, `@Qualifier("name")` | Disambiguate multiple candidates |
| `@Lazy(False)` | Force eager creation (singletons are lazy by default) |
| `@Order(n)` | Order `list[T]` injection, aspects, post-processors |
| `@PostConstruct`, `@PreDestroy` | Run after injection / on `close()` in reverse creation order |
| `@Profile("dev")` | Sugar for `@ConditionalOnProfile` |

## Injection rules

Constructor parameters resolve by type hint:

- concrete type or ABC/`Protocol` base (subclass match),
- `list[T]` — all beans of `T`, ordered by `@Order`,
- `T | None` — `None` when absent,
- `Annotated[T, Qualifier("name")]` — by bean name,
- forward references and `from __future__ import annotations`.

Ambiguity without `@Primary`/qualifier raises `NoUniqueBean` naming all
candidates; circular dependencies fail at `refresh()` with the full path.
Scopes are `Scope.SINGLETON` (default) and `Scope.PROTOTYPE`.

## Conditions and the report

Every definition can be gated by conditions evaluated at `refresh()`:

`@ConditionalOnClass`, `@ConditionalOnMissingClass`, `@ConditionalOnBean`,
`@ConditionalOnMissingBean`, `@ConditionalOnProperty`, `@ConditionalOnProfile`,
or a custom `Condition` via `@Conditional(...)`. `@ConditionalOnMissingBean`
evaluates in a second phase, after all unconditional definitions — the same
two-phase rule as Spring.

`ctx.condition_report` is a `ConditionEvaluationReport` listing every
conditional definition, matched or not, with human-readable reasons — the
answer to "why does this bean (not) exist?".

## AOP

`MethodInterceptor` beans apply to bean methods matched by a `Pointcut`,
ordered by `@Order` (lower = outermost). Pointcuts compose:

```python
from pyleaf.core import Around, Aspect, Invocation, name_matches, within


@Aspect
class LoggingAspect:
    @Around(within("myapp.services") & name_matches("find_*"))
    def log(self, inv: Invocation):
        try:
            return inv.proceed()
        finally:
            ...
```

Advice forms: `@Around` (the primitive), `@Before`, `@After`,
`@AfterReturning`, `@AfterThrowing`. Other pointcut primitives:
`annotated(Marker)`, `bean_type(T)`; operators `&`, `|`, `~`.

Methods are wrapped **on the instance** via `wrapt`, so `isinstance` holds,
`inspect.signature` is preserved, and `self.method()` calls are intercepted
— a deliberate difference from Spring's proxy model. Dunder methods,
properties, classmethods and staticmethods are never wrapped; beans with no
matching pointcut pay zero overhead. Async coroutine methods are supported;
a sync interceptor around an async method fails at startup.

## Events and environment

`@EventListener`-annotated bean methods declare the event type in their
single parameter annotation and receive any published event that is an
instance of it (base classes included) — events are plain objects, no base
class required. Beans inject `EventPublisher` (the context registers itself
under that structural type) and call `publish(event)`; `publish_async`
awaits async listeners sequentially. Listener dispatch order honors
`@Order`.

`Environment.get("a.b.c", int)` reads layered `PropertySource`s (later
sources override earlier) with `EnvVarSource` for process environment
variables and active-profile support.

## Errors

`PyleafError` base class; `NoSuchBean`, `NoUniqueBean`,
`CircularDependency`, `BeanCreationError`.
