"""TodoService: the business layer between controller and repository.

Injects the request-scoped ``RequestAudit`` (as a proxy) so every service
call is recorded against the current request — a singleton bean holding a
per-request collaborator, the classic Spring request-scope pattern.
"""

from pyleaf.core import Service

from .audit import RequestAudit
from .model import Todo, TodoCreate, TodoNotFoundError, TodoUpdate
from .repository import TodoRepository


@Service
class TodoService:
    """CRUD service; raises ``TodoNotFoundError`` for missing ids."""

    def __init__(self, repository: TodoRepository, audit: RequestAudit) -> None:
        self._repository = repository
        self._audit = audit  # RequestScopedProxy — resolves per request

    def list_todos(self) -> list[Todo]:
        """Every todo."""
        self._audit.record("list")
        return self._repository.list_all()

    def get_todo(self, todo_id: int) -> Todo:
        """One todo; ``TodoNotFoundError`` when the id is missing."""
        self._audit.record(f"get:{todo_id}")
        todo = self._repository.get(todo_id)
        if todo is None:
            raise TodoNotFoundError(f"todo {todo_id} not found")
        return todo

    def create_todo(self, payload: TodoCreate) -> Todo:
        """Insert a todo from the POST body."""
        self._audit.record("create")
        return self._repository.add(payload.title, payload.completed)

    def update_todo(self, todo_id: int, payload: TodoUpdate) -> Todo:
        """Apply a PUT body; ``TodoNotFoundError`` when the id is missing."""
        self._audit.record(f"update:{todo_id}")
        todo = self._repository.update(
            todo_id, title=payload.title, completed=payload.completed
        )
        if todo is None:
            raise TodoNotFoundError(f"todo {todo_id} not found")
        return todo

    def delete_todo(self, todo_id: int) -> None:
        """Delete a todo; ``TodoNotFoundError`` when the id is missing."""
        self._audit.record(f"delete:{todo_id}")
        if not self._repository.delete(todo_id):
            raise TodoNotFoundError(f"todo {todo_id} not found")
