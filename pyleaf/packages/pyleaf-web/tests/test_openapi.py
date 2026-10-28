"""OpenAPI parity: class-routed schema equals a function-routed reference."""

from fastapi import FastAPI
from pydantic import BaseModel
from pyleaf.core import ApplicationContext
from pyleaf.web import GetMapping, PostMapping, RestController, build_router


class Todo(BaseModel):
    id: int
    title: str


class TodoIn(BaseModel):
    title: str


def test_openapi_parity_with_function_routes() -> None:
    @RestController("/todos")
    class TodoController:
        @GetMapping("/{todo_id}")
        def get_todo(self, todo_id: int) -> Todo:
            return Todo(id=todo_id, title="x")

        @GetMapping("")
        def list_todos(self, limit: int = 10) -> list[Todo]:
            return [Todo(id=i, title=f"t{i}") for i in range(limit)]

        @PostMapping("")
        def create_todo(self, body: TodoIn) -> Todo:
            return Todo(id=1, title=body.title)

    ctx = ApplicationContext()
    ctx.register(TodoController)
    ctx.refresh()

    class_app = FastAPI()
    class_app.include_router(build_router(ctx))

    ref_app = FastAPI()

    @ref_app.get("/todos/{todo_id}")
    def get_todo(todo_id: int) -> Todo:
        return Todo(id=todo_id, title="x")

    @ref_app.get("/todos")
    def list_todos(limit: int = 10) -> list[Todo]:
        return [Todo(id=i, title=f"t{i}") for i in range(limit)]

    @ref_app.post("/todos")
    def create_todo(body: TodoIn) -> Todo:
        return Todo(id=1, title=body.title)

    assert class_app.openapi() == ref_app.openapi()
