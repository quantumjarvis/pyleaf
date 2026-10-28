"""TodoRepository: CRUD persistence over the SQLAlchemy starter beans.

The starter's ``engine`` and ``sessionfactory`` beans are injected by type —
``app.datasource.url`` in ``application.yaml`` selects the database, so no
wiring code exists in this app.
"""

from pyleaf.core import PostConstruct, Repository
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .model import Base, Todo, TodoEntity


@Repository
class TodoRepository:
    """CRUD over the ``todos`` table via the auto-configured sessionmaker."""

    def __init__(self, engine: Engine, sessionfactory: sessionmaker) -> None:
        self._engine = engine
        self._sessionfactory = sessionfactory

    @PostConstruct
    def create_schema(self) -> None:
        """Create the ``todos`` table when the repository bean is built."""
        Base.metadata.create_all(self._engine)

    def list_all(self) -> list[Todo]:
        """Every todo, ordered by id."""
        with self._sessionfactory() as session:
            rows = session.scalars(
                select(TodoEntity).order_by(TodoEntity.id)
            ).all()
            return [self._to_domain(row) for row in rows]

    def get(self, todo_id: int) -> Todo | None:
        """The todo with ``todo_id``, or ``None``."""
        with self._sessionfactory() as session:
            row = session.get(TodoEntity, todo_id)
            return self._to_domain(row) if row is not None else None

    def add(self, title: str, completed: bool) -> Todo:
        """Insert and return a new todo."""
        with self._sessionfactory() as session:
            row = TodoEntity(title=title, completed=completed)
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_domain(row)

    def update(
        self,
        todo_id: int,
        *,
        title: str | None = None,
        completed: bool | None = None,
    ) -> Todo | None:
        """Apply the non-``None`` fields; ``None`` when the id is missing."""
        with self._sessionfactory() as session:
            row = session.get(TodoEntity, todo_id)
            if row is None:
                return None
            if title is not None:
                row.title = title
            if completed is not None:
                row.completed = completed
            session.commit()
            session.refresh(row)
            return self._to_domain(row)

    def delete(self, todo_id: int) -> bool:
        """Delete the todo; ``False`` when the id did not exist."""
        with self._sessionfactory() as session:
            row = session.get(TodoEntity, todo_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    @staticmethod
    def _to_domain(row: TodoEntity) -> Todo:
        return Todo(id=row.id, title=row.title, completed=row.completed)
