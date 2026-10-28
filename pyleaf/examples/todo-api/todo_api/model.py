"""Todo domain model: SQLAlchemy entity, domain record and API schemas."""

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Boolean, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the todo-api schema."""


class TodoEntity(Base):
    """The ``todos`` table row."""

    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)


@dataclass(frozen=True)
class Todo:
    """Detached domain record handed between repository, service, controller.

    Entities are mapped into ``Todo`` inside the session so nothing touches
    expired ORM attributes after the session closes.
    """

    id: int
    title: str
    completed: bool


class TodoCreate(BaseModel):
    """``POST /todos`` request body."""

    title: str
    completed: bool = False


class TodoUpdate(BaseModel):
    """``PUT /todos/{id}`` request body; ``None`` fields are left unchanged."""

    title: str | None = None
    completed: bool | None = None


class TodoOut(BaseModel):
    """Serialized todo returned by every endpoint."""

    id: int
    title: str
    completed: bool

    @classmethod
    def from_todo(cls, todo: Todo) -> "TodoOut":
        """Map a domain record into the API schema."""
        return cls(id=todo.id, title=todo.title, completed=todo.completed)


class TodoNotFoundError(Exception):
    """Raised for a missing todo id; mapped to 404 by ``TodoAdvice``."""
