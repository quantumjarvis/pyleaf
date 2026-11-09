"""BankController: the REST routes, plus the e2e probe endpoints.

Every route requires authentication — ``pyleaf.security.default-access``
is ``authenticated``, so the ``SecurityMiddleware`` returns 401 to
anonymous callers before they reach a controller. ``POST /transfer``
additionally carries ``@Secured("TELLER")``: the method-security advice
(@Order 50 — outside the tx interceptor) maps a missing token to 401
and an authenticated caller without the role to 403.

``/probe/outbox`` exists for the e2e: it exposes the stub mailer's
recorded sends so ``scripts/e2e_m3.sh`` can assert exactly-once
post-commit delivery over plain HTTP.
"""

from pyleaf.security import Secured
from pyleaf.web import GetMapping, PostMapping, RestController

from .model import (
    AccountCreate,
    AccountOut,
    AuditOut,
    MailOut,
    TransferReceipt,
    TransferRequest,
)
from .notify import StubMailer
from .service import AuditService, TransferService


@RestController
class BankController:
    """REST facade over :class:`TransferService`."""

    def __init__(self, service: TransferService) -> None:
        self._service = service

    @PostMapping("/accounts", status_code=201)
    def create_account(self, payload: AccountCreate) -> AccountOut:
        """``POST /accounts`` — open an account (authenticated)."""
        return AccountOut.from_record(
            self._service.create_account(
                payload.owner, payload.balance_cents
            )
        )

    @GetMapping("/accounts")
    def list_accounts(self) -> list[AccountOut]:
        """``GET /accounts`` — every account (authenticated)."""
        return [
            AccountOut.from_record(a) for a in self._service.list_accounts()
        ]

    @GetMapping("/accounts/{account_id}")
    def get_account(self, account_id: int) -> AccountOut:
        """``GET /accounts/{id}`` — one account or the advice's 404."""
        return AccountOut.from_record(self._service.get_account(account_id))

    @PostMapping("/transfer")
    @Secured("TELLER")
    def transfer(self, payload: TransferRequest) -> TransferReceipt:
        """``POST /transfer`` — role TELLER only; atomic or rolled back."""
        source, target = self._service.transfer(
            payload.from_account_id,
            payload.to_account_id,
            payload.amount_cents,
        )
        return TransferReceipt(
            from_account_id=source.id,
            to_account_id=target.id,
            amount_cents=payload.amount_cents,
            from_balance_cents=source.balance_cents,
            to_balance_cents=target.balance_cents,
        )


@RestController("/audits")
class AuditController:
    """The transfer audit trail — attempt rows persisted REQUIRES_NEW."""

    def __init__(self, audits: AuditService) -> None:
        self._audits = audits

    @GetMapping("")
    def list_audits(self) -> list[AuditOut]:
        """``GET /audits`` — every recorded attempt (authenticated)."""
        return [
            AuditOut.from_record(a) for a in self._audits.list_attempts()
        ]


@RestController("/probe")
class ProbeController:
    """E2E observability: the stub mailer's outbox over HTTP."""

    def __init__(self, mailer: StubMailer) -> None:
        self._mailer = mailer

    @GetMapping("/outbox")
    def outbox(self) -> list[MailOut]:
        """``GET /probe/outbox`` — every mail "sent" since boot."""
        return [MailOut.from_mail(m) for m in self._mailer.outbox]
