"""TodoAdvice: maps domain exceptions to HTTP responses.

Only the domain error is mapped: ``TodoNotFoundError`` -> 404 with a
``{"detail": ...}`` body. FastAPI's own ``RequestValidationError`` handling
sits in a middleware layer *inside* the advice dispatcher, so malformed
bodies keep their standard 422 — validation passes through untouched.
"""

from typing import Any

from pyleaf.web import ControllerAdvice, ExceptionHandler

from .model import TodoNotFoundError


@ControllerAdvice
class TodoAdvice:
    """Domain-exception handlers shared by all controllers."""

    @ExceptionHandler(TodoNotFoundError, status_code=404)
    def todo_not_found(self, exc: TodoNotFoundError) -> dict[str, Any]:
        """Render a missing todo as a 404 JSON body."""
        return {"detail": str(exc), "error": "todo_not_found"}
