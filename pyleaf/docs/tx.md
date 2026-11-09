# pyleaf-tx

`pyleaf.tx` is the transaction layer: the `@Transactional` marker and
its AOP interceptor, the propagation state machine, the programmatic
`TransactionTemplate`, and `@TransactionalEventListener` for
phase-bound event delivery. It depends only on pyleaf-core — the
concrete resource managers live in
`pyleaf.starter.sqlalchemy.tx`
(`SqlAlchemyTransactionManager` / `AsyncSqlAlchemyTransactionManager`),
created automatically once `app.datasource.url` is configured (see
[pyleaf-data](packages/data.md)).

Installing `pyleaf-tx` registers `TxAutoConfiguration` in the
`pyleaf.autoconfigure` entry-point group, so `PyleafApplication.run`
picks up the `transactionInterceptor` aspect and the
`txEventListenerPostProcessor` bean automatically — transaction
management is on as soon as a `TransactionManager` bean exists.

## @Transactional

```python
from pyleaf.tx import Propagation, Transactional


@Transactional                                   # bare: all defaults
def transfer(self, from_id: int, to_id: int, amount: int) -> Receipt: ...

@Transactional(
    propagation=Propagation.REQUIRES_NEW,
    isolation=Isolation.SERIALIZABLE,
    read_only=False,
    timeout=30.0,                                # seconds
    rollback_for=(Exception,),                   # default: any Exception
    no_rollback_for=(),
    manager="reportingTxManager",                # named bean, else by type
)
def rebuild(self) -> None: ...
```

- The marker works on methods or whole classes; a method-level mark
  overrides the class-level one entirely (Spring semantics).
- The manager is resolved lazily per method: `manager="name"` looks the
  bean up by name, otherwise by protocol type — `TransactionManager`
  for sync methods, `AsyncTransactionManager` for async ones. Sync and
  async advices run the *same* boundary executor as
  `TransactionTemplate`, so decorator and template semantics are
  identical.
- A `@Transactional` method with no resolvable manager fails at first
  invocation with a `TransactionError` naming the method — never at
  refresh.
- Generator functions are rejected at `refresh()` with `TypeError`.
- Self-invocation (`self.helper()` inside the bean) **is** intercepted —
  the AOP layer wraps the instance methods themselves.

### Propagation

| Value | Behaviour |
|---|---|
| `REQUIRED` (default) | join the current transaction, or open one |
| `REQUIRES_NEW` | suspend the current transaction, open an independent one, resume after |
| `NESTED` | join via a savepoint when a transaction exists, else open one |
| `SUPPORTS` | join if present, run untransacted otherwise |
| `NOT_SUPPORTED` | suspend the current transaction, run untransacted |
| `MANDATORY` | require an existing transaction — `NoTransactionError` otherwise |
| `NEVER` | require no transaction — `IllegalTransactionStateError` otherwise |

A joined (`is_new=False`) boundary never commits: the outer boundary
drives completion, and a joined failure only marks the shared
transaction rollback-only — a later outer commit then rolls back and
raises `UnexpectedRollbackError`.

### Rollback rules

Deliberately broader than Spring's default: a transaction rolls back
for **any `Exception` subclass** unless it matches `no_rollback_for` —
`isinstance(exc, rollback_for) and not isinstance(exc, no_rollback_for)`.
`BaseException`-only failures (`KeyboardInterrupt`, `SystemExit`,
`GeneratorExit`) never roll back unless listed in `rollback_for`.

### Options

- `isolation` — `Isolation.DEFAULT` is a no-op; other levels emit
  `SET TRANSACTION ISOLATION LEVEL` on postgres / `SET SESSION
  TRANSACTION ISOLATION LEVEL` on mysql+mariadb, and are ignored on
  sqlite (documented best effort).
- `read_only` — `SET TRANSACTION READ ONLY` on postgres plus a
  `before_flush` guard that raises `TransactionError` on any dialect.
- `timeout` — recorded on the status; enforced best-effort via
  `statement_timeout` on postgres only.

## TransactionTemplate

```python
from pyleaf.tx import TransactionTemplate


template = TransactionTemplate(tx_manager)          # REQUIRED defaults
receipt = template.execute(lambda: do_transfer())

with template.required() as status:                  # or `async with`
    do_work()
```

`execute(fn)` runs `fn` in a boundary built from the template's
`TransactionDefinition`; coroutine functions take the async path.
`with_definition(defn)` derives a template on the same manager.

## @TransactionalEventListener

```python
from pyleaf.tx import TransactionalEventListener, TxPhase


@TransactionalEventListener(phase=TxPhase.AFTER_COMMIT)
def on_transfer_completed(self, event: TransferCompleted) -> None:
    self._mailer.send(...)
```

Like `@EventListener`, but delivery is bound to a transaction phase:
`BEFORE_COMMIT`, `AFTER_COMMIT`, `AFTER_ROLLBACK`, or
`AFTER_COMPLETION`. Published inside a transaction, the event is queued
on a per-transaction synchronization and delivered **exactly once** when
the boundary reaches the phase — `AFTER_COMMIT` listeners never see
events from rolled-back transactions. `BEFORE_COMMIT` listener
failures roll the transaction back; later-phase failures are logged.
Published with no active transaction, `fallback_execution=True`
(default) invokes the listener immediately, `False` drops it.

Listeners must be synchronous — phase delivery happens inside the
synchronization hooks, so coroutine listeners are rejected at
decoration time.

## Properties

| Property | Default | Meaning |
|---|---|---|
| `pyleaf.tx.order` | `100` | `@Order` of the tx advice — outermost, so a denied security check never opens a transaction |

See [pyleaf-data](packages/data.md) for `CrudRepository` (repository
methods join the ambient transaction and raise `NoTransactionError`
outside one), `@EnableTransactionManagement`, and the multi-datasource
pattern.
