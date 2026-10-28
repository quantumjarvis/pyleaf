# pyleaf-boot

`pyleaf.boot` turns a `pyleaf.core` context into a runnable application:
configuration loading, entry-point auto-configuration, a `run()` entry
point with banner and startup timing, runner beans, and graceful shutdown.

## PyleafApplication

```python
from pyleaf.boot import PyleafApplication

ctx = PyleafApplication.run("myapp", config_dir="myapp")
```

`run()` executes the full boot sequence:

1. `load_environment` builds the layered `Environment` and default logging
   (`pyleaf.logging.*` properties);
2. prints the banner (suppress with `banner=False` or `pyleaf.banner=false`);
3. creates the `ApplicationContext` and component-scans `myapp`;
4. discovers and registers auto-configurations via entry points;
5. `ctx.refresh(eager=True)`;
6. invokes `ApplicationRunner` / `CommandLineRunner` beans in `@Order`;
7. installs SIGINT/SIGTERM handlers that close the context within
   `pyleaf.shutdown.timeout` seconds (`install_signal_handlers`, `shutdown`).

## Configuration loading

`load_environment(config_dir, profiles=..., args=...)` produces an
`Environment` whose sources are ordered lowest-to-highest precedence:

1. `application.yaml`
2. `application-<profile>.yaml` for each active profile
3. environment variables (`APP_DATASOURCE_URL` → `app.datasource.url`)
4. `--key=value` CLI arguments

## @ConfigurationProperties

```python
from pydantic import BaseModel
from pyleaf.boot import ConfigurationProperties


@ConfigurationProperties(prefix="app.datasource")
class DataSourceProperties(BaseModel):
    url: str
    pool_size: int = 5
    echo: bool = False
```

A `@ConfigurationProperties` class registers as a bean with no
`@Component` needed; environment keys under the prefix bind into the
pydantic model with validation. Invalid values fail at startup with the
key path in the error. It can also be used as a `@Bean`-method parameter —
the binding happens on demand.

## Auto-configuration

```python
from pyleaf.boot import AutoConfiguration, AutoConfigureAfter
from pyleaf.core import Bean, ConditionalOnClass, ConditionalOnMissingBean


@AutoConfiguration
@AutoConfigureAfter(SqlAlchemyAutoConfiguration)
@ConditionalOnClass("sqlalchemy")
class MyAutoConfiguration:
    @Bean
    @ConditionalOnMissingBean(MyClient)
    def myClient(self) -> MyClient: ...
```

- `@AutoConfiguration` marks a class as an auto-configuration; ordering is
  `@AutoConfigureAfter`/`@AutoConfigureBefore` (topological) then
  `@AutoConfigureOrder`.
- Auto-configs are discovered through the `pyleaf.autoconfigure`
  entry-point group (`discover_autoconfigs`) and registered **after** user
  beans (`register_autoconfigs`), so user definitions win
  `@ConditionalOnMissingBean` — the backoff mechanism.
- `pyleaf.autoconfigure.exclude` disables named auto-configurations.
- `sort_autoconfigs` and `is_autoconfiguration` are the ordering and
  introspection helpers behind discovery.

Every conditional outcome — matched or not — lands in
`ctx.condition_report`, so an application can always explain why a
starter's beans did or did not appear.
