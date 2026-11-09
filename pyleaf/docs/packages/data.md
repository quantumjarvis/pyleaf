# pyleaf-data

The data layer: typed repositories over SQLAlchemy, the data
auto-configuration (`DataAutoConfiguration`), Alembic migrate-on-start,
and the multi-datasource pattern.

Installing `pyleaf-data` registers `DataAutoConfiguration` in the
`pyleaf.autoconfigure` entry-point group (`data`), so
`PyleafApplication.run` picks it up automatically.

## Repositories as beans

```python
from pyleaf.data import CrudRepository

class ItemRepo(CrudRepository[Item, int], bean=True):
    ...
```

`bean=True` applies the `@Repository` stereotype, so a package scan or a
plain `ctx.register(ItemRepo)` registers the class like any other
component. The repository injects the transaction manager and every
operation joins the ambient transaction — outside a `@Transactional`
boundary it raises `NoTransactionError`. No extra registration hook is
needed: scanning picks repository beans up end to end.

## @EnableTransactionManagement

```python
from pyleaf.core import Configuration
from pyleaf.data import EnableTransactionManagement

@Configuration
@EnableTransactionManagement
class AppConfig:
    ...
```

An explicit opt-in declaration, for parity with Spring — and, like
Spring, it is *not strictly required*: in pyleaf the `@Transactional`
interceptor is registered by pyleaf-tx's `tx` auto-configuration
whenever it is installed, and transaction management is on as soon as a
`TransactionManager` bean exists. The marker stamps a flag on the
configuration class and registers a `transactionManagementMarker` bean,
so the declaration is detectable from the context and the condition
report.

## Alembic migrate-on-start

```yaml
# application.yaml
pyleaf:
  data:
    migrate-on-start: true
    migrations-dir: "./migrations"   # default
```

When `pyleaf.data.migrate-on-start` is set and `alembic` is installed,
the `alembicMigrationRunner` bean runs `alembic upgrade head` during
`ctx.refresh()` — before runners, web serving, or any app code. A
migration failure fails the refresh; a database already at head is a
no-op. A present `migrate-on-start=false` creates the bean but never
touches the database; an absent property drops the bean definition
entirely with a `@ConditionalOnProperty` report reason.

The runner shares the application `Engine` with `env.py` in two ways:
`sqlalchemy.url` is set on the `alembic.config.Config` (password
included, for vanilla envs that call `engine_from_config`), and the
`Engine` itself is stashed under `config.attributes["connection"]` —
the alembic cookbook convention — so `env.py` can reuse it:

```python
def run_migrations_online() -> None:
    connectable = context.config.attributes.get("connection")
    if connectable is None:
        connectable = engine_from_config(
            context.config.get_section(context.config.config_ini_section),
            prefix="sqlalchemy.",
        )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
```

## Multiple datasources

For a single datasource the starter's `app.datasource.url` engine is the
primary datasource. For a *second* datasource, define the whole pair
yourself — any user `Engine` bean already backs the starter's
`@ConditionalOnMissingBean` engine off:

```python
from typing import Annotated
from pyleaf.core import Bean, Configuration, Primary, Qualifier

@Configuration
class DataSources:
    @Bean
    @Primary
    def engine(self) -> Engine: ...          # primary: unqualified get wins

    @Bean(name="reporting")
    def reporting_engine(self) -> Engine: ...

    @Bean
    @Primary
    def txManager(self, engine: Engine) -> SqlAlchemyTransactionManager: ...

    @Bean(name="reportingTxManager")
    def reporting_tm(
        self, engine: Annotated[Engine, Qualifier("reporting")]
    ) -> SqlAlchemyTransactionManager: ...
```

Contract:

- `ctx.get(Engine)` unqualified resolves the `@Primary` bean;
- `Annotated[Engine, Qualifier("reporting")]` resolves the secondary —
  at injection points and at `ctx.get` alike;
- two unqualified engines with no `@Primary` → `NoUniqueBean`;
- a bare `@Transactional` resolves the primary `TransactionManager`,
  `@Transactional(manager="reportingTxManager")` the secondary — each
  manager binds its session under its own `sessionmaker`, so a
  transaction on one datasource never touches the other (commit/rollback
  are isolated per datasource);
- a repository joins a datasource by taking its (qualified) transaction
  manager as a constructor dependency.
