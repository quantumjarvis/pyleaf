# pyleaf-starter-sqlalchemy

The reference starter: proof that the starter model works end to end, and
the template for writing your own.

Installing `pyleaf-starter-sqlalchemy` registers
`SqlAlchemyAutoConfiguration` in the `pyleaf.autoconfigure` entry-point
group, so `PyleafApplication.run` picks it up automatically.

## What it provides

When `sqlalchemy` is importable and `app.datasource.url` is configured:

- an `Engine` bean built from `app.datasource.*`
  (`url`, `pool_size`, `echo`), and
- a `sessionmaker` bean bound to whichever `Engine` survived.

```yaml
# application.yaml
app:
  datasource:
    url: "sqlite:///:memory:"
    pool_size: 5
    echo: false
```

Transactions are not part of M1 — they arrive in M3 with `pyleaf-tx` /
`pyleaf-tx-sqlalchemy`, built on the M1 interceptor chain.

## The three starter behaviours

| Situation | Result | Report entry |
|---|---|---|
| Starter installed, `app.datasource.url` set | `Engine` + `sessionmaker` beans created | matched |
| User defines their own `Engine` `@Bean` | starter `engine` def backs off; `sessionfactory` still wires to the user's engine | `@ConditionalOnMissingBean` unmatched, naming the user bean |
| `sqlalchemy` not installed | every starter def dropped, nothing fails | `@ConditionalOnClass: sqlalchemy is not importable` |
| `app.datasource.url` absent | `engine` def dropped via `@ConditionalOnProperty` | unmatched, missing property |

All four outcomes are inspectable through `ctx.condition_report`.

## Writing a starter

1. Package an `@AutoConfiguration` class; gate it and each `@Bean` method
   with `@ConditionalOnClass`, `@ConditionalOnMissingBean`,
   `@ConditionalOnProperty` as appropriate.
2. Declare the entry point:

   ```toml
   [project.entry-points."pyleaf.autoconfigure"]
   mystarter = "mypkg.autoconfigure:MyAutoConfiguration"
   ```

3. Keep the module importable **without** the optional dependency —
   this starter shows the pattern: `try/except ImportError` placeholder
   classes for annotations, real imports inside method bodies, and
   `@ConditionalOnClass` on every definition so the defs drop cleanly
   with a report reason instead of an import error.

Exported symbols: `SqlAlchemyAutoConfiguration`, `DataSourceProperties`
(from `pyleaf.starter.sqlalchemy.autoconfigure`).
