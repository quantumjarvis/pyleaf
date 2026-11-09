"""Bank domain model: SQLAlchemy entities, domain records, API schemas.

Money is carried as integer ``*_cents`` — a transfer either moves an
exact number of cents or rolls back, and integer math keeps the
assertion arithmetic exact.
"""

from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the bank schema."""


class Account(Base):
    """The ``accounts`` table row — balances in cents."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String(100))
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)


class TransferAudit(Base):
    """The ``transfer_audit`` table row — one row per transfer ATTEMPT.

    Rows are written by ``AuditService.record_attempt`` inside a
    REQUIRES_NEW transaction, so an attempt is on record even when the
    transfer itself rolls back.
    """

    __tablename__ = "transfer_audit"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    from_account_id: Mapped[int] = mapped_column(Integer)
    to_account_id: Mapped[int] = mapped_column(Integer)
    amount_cents: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(20), default="attempt")


@dataclass(frozen=True)
class AccountRecord:
    """Detached account handed between repository, service, controller.

    Entities are mapped into records inside the transaction so nothing
    touches expired ORM attributes after the session closes.
    """

    id: int
    owner: str
    balance_cents: int


@dataclass(frozen=True)
class AuditRecord:
    """Detached audit row for the ``GET /audits`` endpoint."""

    id: int
    from_account_id: int
    to_account_id: int
    amount_cents: int
    outcome: str


@dataclass(frozen=True)
class TransferCompleted:
    """Published by ``TransferService.transfer`` inside the transaction.

    The ``AFTER_COMMIT`` listener only sees it when the transfer actually
    commits — a rolled-back transfer never reaches the mailer.
    """

    from_account_id: int
    to_account_id: int
    amount_cents: int


@dataclass(frozen=True)
class SentMail:
    """One entry in the stub mailer's outbox."""

    to: str
    subject: str
    body: str


class AccountCreate(BaseModel):
    """``POST /accounts`` request body."""

    owner: str
    balance_cents: int = Field(default=0, ge=0)


class AccountOut(BaseModel):
    """Serialized account returned by the account endpoints."""

    id: int
    owner: str
    balance_cents: int

    @classmethod
    def from_record(cls, record: AccountRecord) -> "AccountOut":
        """Map a domain record into the API schema."""
        return cls(
            id=record.id,
            owner=record.owner,
            balance_cents=record.balance_cents,
        )


class TransferRequest(BaseModel):
    """``POST /transfer`` request body."""

    from_account_id: int
    to_account_id: int
    amount_cents: int = Field(gt=0)


class TransferReceipt(BaseModel):
    """``POST /transfer`` response body."""

    from_account_id: int
    to_account_id: int
    amount_cents: int
    from_balance_cents: int
    to_balance_cents: int


class AuditOut(BaseModel):
    """Serialized audit row for ``GET /audits``."""

    id: int
    from_account_id: int
    to_account_id: int
    amount_cents: int
    outcome: str

    @classmethod
    def from_record(cls, record: AuditRecord) -> "AuditOut":
        """Map a domain record into the API schema."""
        return cls(
            id=record.id,
            from_account_id=record.from_account_id,
            to_account_id=record.to_account_id,
            amount_cents=record.amount_cents,
            outcome=record.outcome,
        )


class MailOut(BaseModel):
    """Serialized stub-mailer entry for ``GET /probe/outbox``."""

    to: str
    subject: str
    body: str

    @classmethod
    def from_mail(cls, mail: SentMail) -> "MailOut":
        """Map a stub outbox entry into the API schema."""
        return cls(to=mail.to, subject=mail.subject, body=mail.body)


class InsufficientFundsError(Exception):
    """Raised when a debit would overdraw the source account.

    Raised AFTER the debit inside the transaction — the mid-way failure
    the M3 e2e exists to prove rolls back — and mapped to 422 by
    ``BankAdvice``.
    """


class AccountNotFoundError(Exception):
    """Raised for a missing account id; mapped to 404 by ``BankAdvice``."""
