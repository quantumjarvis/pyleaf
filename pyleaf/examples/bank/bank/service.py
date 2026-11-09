"""Services: the transactional business layer.

``TransferService.transfer`` is the M3 showcase:

1. the attempt is audited through ``AuditService.record_attempt`` —
   ``REQUIRES_NEW`` suspends the transfer tx and commits the audit row
   in its own transaction, so the record survives a rollback;
2. the debit lands, THEN the balance is checked — an overdraw raises
   ``InsufficientFundsError`` mid-way through the boundary and rolls
   the debit back (the audit row stays);
3. on success a ``TransferCompleted`` event is published — the
   ``AFTER_COMMIT`` listener delivers it exactly once, only on commit.

Audit-before-debit ordering matters on sqlite: the file supports a
single writer, so the inner REQUIRES_NEW transaction must commit before
the outer transaction takes its write lock. (On postgres the audit call
could sit anywhere in the method.)
"""

from pyleaf.core import EventPublisher, Service
from pyleaf.data import Sort
from pyleaf.tx import Propagation, Transactional

from .model import (
    Account,
    AccountNotFoundError,
    AccountRecord,
    AuditRecord,
    InsufficientFundsError,
    TransferAudit,
    TransferCompleted,
)
from .repository import AccountRepository, AuditRepository

_BY_ID = Sort.by("id")


@Service
class AuditService:
    """The audit trail — writes in REQUIRES_NEW, reads in REQUIRED."""

    def __init__(self, audits: AuditRepository) -> None:
        self._audits = audits

    @Transactional(propagation=Propagation.REQUIRES_NEW)
    def record_attempt(
        self, from_account_id: int, to_account_id: int, amount_cents: int
    ) -> None:
        """Persist a transfer ATTEMPT in a fresh, independent transaction.

        Suspends the caller's transaction, writes and commits the audit
        row on a separate session, then resumes — the row survives the
        transfer's rollback.
        """
        self._audits.save(
            TransferAudit(
                from_account_id=from_account_id,
                to_account_id=to_account_id,
                amount_cents=amount_cents,
                outcome="attempt",
            )
        )

    @Transactional
    def list_attempts(self) -> list[AuditRecord]:
        """Every audit row, ordered by id."""
        return [
            AuditRecord(
                id=row.id,
                from_account_id=row.from_account_id,
                to_account_id=row.to_account_id,
                amount_cents=row.amount_cents,
                outcome=row.outcome,
            )
            for row in self._audits.find_all(sort=_BY_ID)
        ]


@Service
class TransferService:
    """Account CRUD plus the ``@Transactional`` transfer boundary."""

    def __init__(
        self,
        accounts: AccountRepository,
        audit: AuditService,
        events: EventPublisher,
    ) -> None:
        self._accounts = accounts
        self._audit = audit
        self._events = events

    @Transactional
    def list_accounts(self) -> list[AccountRecord]:
        """Every account, ordered by id."""
        return [
            self._to_record(row) for row in self._accounts.find_all(sort=_BY_ID)
        ]

    @Transactional
    def get_account(self, account_id: int) -> AccountRecord:
        """One account; ``AccountNotFoundError`` when the id is missing."""
        row = self._accounts.find_by_id(account_id)
        if row is None:
            raise AccountNotFoundError(f"account {account_id} not found")
        return self._to_record(row)

    @Transactional
    def create_account(self, owner: str, balance_cents: int) -> AccountRecord:
        """Insert and return a new account."""
        row = self._accounts.save(
            Account(owner=owner, balance_cents=balance_cents)
        )
        # Flush inside the ambient tx so the autoincrement id is assigned
        # before the record is mapped and returned.
        self._accounts._session().flush()
        return self._to_record(row)

    @Transactional
    def transfer(
        self, from_account_id: int, to_account_id: int, amount_cents: int
    ) -> tuple[AccountRecord, AccountRecord]:
        """Move ``amount_cents`` between two accounts, atomically.

        Returns the post-transfer ``(source, target)`` records. Raises
        ``AccountNotFoundError`` for a missing id and
        ``InsufficientFundsError`` mid-way — AFTER the debit — when the
        source would overdraw; the boundary rolls everything back.
        """
        source = self._accounts.find_by_id(from_account_id)
        target = self._accounts.find_by_id(to_account_id)
        if source is None:
            raise AccountNotFoundError(f"account {from_account_id} not found")
        if target is None:
            raise AccountNotFoundError(f"account {to_account_id} not found")

        # REQUIRES_NEW: the audit row commits in its own transaction and
        # survives whatever happens to the transfer below.
        self._audit.record_attempt(
            from_account_id, to_account_id, amount_cents
        )

        # Debit first, check second: the failure is deliberately mid-way.
        source.balance_cents -= amount_cents
        self._accounts.save(source)
        if source.balance_cents < 0:
            raise InsufficientFundsError(
                f"account {from_account_id} cannot cover"
                f" {amount_cents} cents"
            )

        target.balance_cents += amount_cents
        self._accounts.save(target)

        # Deferred until AFTER_COMMIT — never reaches listeners on rollback.
        self._events.publish(
            TransferCompleted(
                from_account_id=from_account_id,
                to_account_id=to_account_id,
                amount_cents=amount_cents,
            )
        )
        return self._to_record(source), self._to_record(target)

    @staticmethod
    def _to_record(row: Account) -> AccountRecord:
        return AccountRecord(
            id=row.id, owner=row.owner, balance_cents=row.balance_cents
        )
