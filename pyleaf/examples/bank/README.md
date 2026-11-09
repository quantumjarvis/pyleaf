# bank — pyleaf M3 example

A money-transfer service exercising the M3 feature set end to end:
`pyleaf-data` repositories, `pyleaf-tx` declarative transactions and
transaction-bound events, `pyleaf-security` JWT + method security, all
on the M2 web/actuator boot stack. `scripts/e2e_m3.sh` drives it over
real HTTP.

## Run

```bash
cd examples/bank
python -m bank                       # serves on 127.0.0.1:8080
python -m bank --pyleaf.web.port=9000 \
               --app.datasource.url=sqlite:////tmp/bank.db
```

Money is integer **cents** everywhere (`balance_cents`, `amount_cents`).

## Endpoints

| Route | Access | Notes |
|---|---|---|
| `GET /health` | anonymous | readiness probe (`permit_all` rule) |
| `POST /accounts` | authenticated | `{"owner", "balance_cents"}` -> 201 |
| `GET /accounts`, `GET /accounts/{id}` | authenticated | list / fetch |
| `POST /transfer` | role `TELLER` | `{"from_account_id", "to_account_id", "amount_cents"}` |
| `GET /audits` | authenticated | every transfer *attempt* (REQUIRES_NEW) |
| `GET /probe/outbox` | authenticated | stub mailer sends — the e2e probe |
| `GET /actuator/*` | role `ACTUATOR` | protected by the built-in rule |

Tokens are HS256 JWTs signed with `pyleaf.security.jwt.secret`; the
`roles` claim maps to `ROLE_*` authorities (`["TELLER"]` grants
`POST /transfer`). `exp` is required.

## What each file demonstrates

| File | Features |
|---|---|
| `model.py` | SQLAlchemy entities (`Account`, `TransferAudit`), domain records, pydantic API schemas, `TransferCompleted` event, domain errors |
| `repository.py` | `CrudRepository[Account, int]` / `CrudRepository[TransferAudit, int]` beans via `bean=True` — every call joins the ambient transaction; `@PostConstruct` schema creation on the injected `Engine` |
| `service.py` | `@Transactional` boundaries; `REQUIRES_NEW` audit (`AuditService.record_attempt`) whose row survives rollback; the deliberate mid-way `InsufficientFundsError` after the debit; `EventPublisher.publish` inside the tx |
| `notify.py` | `@TransactionalEventListener(phase=AFTER_COMMIT)` — exactly-once post-commit delivery; `StubMailer`, the e2e-observable outbox |
| `controller.py` | `@RestController` routes; `@Secured("TELLER")` method security on `POST /transfer`; the `/probe/outbox` probe |
| `advice.py` | `@ControllerAdvice` — `InsufficientFundsError` -> 422, `AccountNotFoundError` -> 404 |
| `config.py` | `@EnableTransactionManagement` — the explicit (optional) declaration; registers the `transactionManagementMarker` bean |
| `application.yaml` | `app.datasource.url`, `pyleaf.security.jwt.secret`, `pyleaf.security.default-access`, an ordered `permit_all` path rule for `/health` |

## The transfer flow

`TransferService.transfer` is `@Transactional` (REQUIRED):

1. `AuditService.record_attempt` runs `REQUIRES_NEW` — the outer
   transaction suspends, the audit row commits on its own session, the
   outer resumes. **Audit precedes the debit** because sqlite allows a
   single writer: the inner transaction must commit before the outer
   takes its write lock.
2. The source is debited, *then* the balance is checked — overdraw
   raises `InsufficientFundsError` mid-boundary. The debit rolls back;
   the audit row stays.
3. On success `TransferCompleted` is published. The tx event machinery
   defers it to `AFTER_COMMIT` — `NotificationService` sends exactly
   once, and never when the transaction rolls back.
