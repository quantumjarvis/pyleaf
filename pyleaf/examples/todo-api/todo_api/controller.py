"""TodoController: the ``/todos`` REST routes.

The class is a normal bean — the service is constructor-injected — while
``@*Mapping`` methods become FastAPI routes through
``register_controllers``. Path/query params and pydantic bodies are bound
exactly as FastAPI binds plain function endpoints (validation errors stay
422 via FastAPI's own exception middleware).
"""

from pyleaf.web import (
    DeleteMapping,
    GetMapping,
    PostMapping,
    PutMapping,
    RestController,
)

from .model import TodoCreate, TodoOut, TodoUpdate
from .service import TodoService


@RestController("/todos")
class TodoController:
    """REST facade over :class:`TodoService`."""

    def __init__(self, service: TodoService) -> None:
        self._service = service

    @GetMapping("")
    def list_todos(self) -> list[TodoOut]:
        """``GET /todos`` — every todo."""
        return [TodoOut.from_todo(t) for t in self._service.list_todos()]

    @GetMapping("/{todo_id}")
    def get_todo(self, todo_id: int) -> TodoOut:
        """``GET /todos/{todo_id}`` — one todo or the advice's 404."""
        return TodoOut.from_todo(self._service.get_todo(todo_id))

    @PostMapping("", status_code=201)
    def create_todo(self, payload: TodoCreate) -> TodoOut:
        """``POST /todos`` — create and return the new todo."""
        return TodoOut.from_todo(self._service.create_todo(payload))

    @PutMapping("/{todo_id}")
    def update_todo(self, todo_id: int, payload: TodoUpdate) -> TodoOut:
        """``PUT /todos/{todo_id}`` — apply non-null fields."""
        return TodoOut.from_todo(self._service.update_todo(todo_id, payload))

    @DeleteMapping("/{todo_id}", status_code=204)
    def delete_todo(self, todo_id: int) -> None:
        """``DELETE /todos/{todo_id}`` — empty 204 or the advice's 404."""
        self._service.delete_todo(todo_id)
