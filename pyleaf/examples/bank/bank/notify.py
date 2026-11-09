"""Notifications: a stub mailer plus the AFTER_COMMIT listener.

``StubMailer`` records every "sent" mail in memory instead of hitting
SMTP — the stub IS the point: it makes post-commit delivery observable
(``GET /probe/outbox``) so the e2e can assert the transfer notification
arrives exactly once after a commit and never after a rollback.
"""

import logging

from pyleaf.core import Component
from pyleaf.tx import TransactionalEventListener, TxPhase

from .model import SentMail, TransferCompleted

_logger = logging.getLogger(__name__)


@Component
class StubMailer:
    """An outbox instead of an SMTP client — records every send."""

    def __init__(self) -> None:
        self.outbox: list[SentMail] = []

    def send(self, to: str, subject: str, body: str) -> None:
        """Record the mail; a real mailer would call SMTP here."""
        mail = SentMail(to=to, subject=subject, body=body)
        self.outbox.append(mail)
        _logger.info("STUB MAILER sent to=%s subject=%r", mail.to, mail.subject)


@Component
class NotificationService:
    """Sends the transfer notification after the transfer commits."""

    def __init__(self, mailer: StubMailer) -> None:
        self._mailer = mailer

    @TransactionalEventListener(phase=TxPhase.AFTER_COMMIT)
    def on_transfer_completed(self, event: TransferCompleted) -> None:
        """Notify the recipient — runs only after a successful commit.

        The event is published inside the transfer transaction; the tx
        event machinery queues it and delivers it once the outer
        transaction commits — exactly once, and never on rollback.
        """
        cents = event.amount_cents
        self._mailer.send(
            to=f"account-{event.to_account_id}@bank.local",
            subject="Transfer received",
            body=(
                f"{cents} cents arrived from account"
                f" {event.from_account_id}"
            ),
        )
