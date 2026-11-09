"""Repositories: typed ``CrudRepository`` beans over the ambient tx.

``CrudRepository[T, ID]`` resolves every operation through
``current_session(..., require_tx=True)`` — repository methods *join
the caller's transaction* and raise ``NoTransactionError`` outside one,
which is why every service method here is ``@Transactional``.

The repositories inject the starter's ``SqlAlchemyTransactionManager``
(``app.datasource.url`` selects the database — no wiring code in this
app); ``AccountRepository`` additionally takes the ``Engine`` so its
``@PostConstruct`` can create the schema.
"""

from pyleaf.core import PostConstruct
from pyleaf.data import CrudRepository
from pyleaf.starter.sqlalchemy.tx import SqlAlchemyTransactionManager
from sqlalchemy.engine import Engine

from .model import Account, Base, TransferAudit


class AccountRepository(CrudRepository[Account, int], bean=True):
    """CRUD over ``accounts``, joining the ambient transaction."""

    def __init__(
        self,
        tx_manager: SqlAlchemyTransactionManager,
        engine: Engine,
    ) -> None:
        super().__init__(tx_manager)
        self._engine = engine

    @PostConstruct
    def create_schema(self) -> None:
        """Create every table when the repository bean is built."""
        Base.metadata.create_all(self._engine)


class AuditRepository(CrudRepository[TransferAudit, int], bean=True):
    """CRUD over ``transfer_audit``, joining whichever tx is ambient.

    Called inside ``REQUIRES_NEW`` from ``AuditService`` — it resolves
    the session bound to the *inner* transaction, so the audit insert
    commits independently of the transfer's outcome.
    """
