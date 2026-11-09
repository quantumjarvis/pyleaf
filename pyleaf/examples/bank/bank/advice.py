"""BankAdvice: maps domain exceptions to HTTP responses.

``InsufficientFundsError`` -> 422 and ``AccountNotFoundError`` -> 404.
Authentication/authorization failures never reach this dispatcher: the
``SecurityMiddleware`` short-circuits them to 401/403 first (it also
maps ``AuthenticationError``/``AccessDeniedError`` escaping downstream,
so no handler for those is needed here).
"""

from typing import Any

from pyleaf.web import ControllerAdvice, ExceptionHandler

from .model import AccountNotFoundError, InsufficientFundsError


@ControllerAdvice
class BankAdvice:
    """Domain-exception handlers shared by all controllers."""

    @ExceptionHandler(InsufficientFundsError, status_code=422)
    def insufficient_funds(self, exc: InsufficientFundsError) -> dict[str, Any]:
        """Render an overdrawn transfer as a 422 JSON body."""
        return {"detail": str(exc), "error": "insufficient_funds"}

    @ExceptionHandler(AccountNotFoundError, status_code=404)
    def account_not_found(self, exc: AccountNotFoundError) -> dict[str, Any]:
        """Render a missing account as a 404 JSON body."""
        return {"detail": str(exc), "error": "account_not_found"}
