"""Shared ORM model + repository beans for the pyleaf-data tests."""

from pyleaf.data import AsyncCrudRepository, CrudRepository
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the pyleaf-data test tables."""


class Item(Base):
    """Entity with two sortable fields and an autoincrement pk."""

    __tablename__ = "data_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    price: Mapped[int] = mapped_column()


class ItemRepo(CrudRepository[Item, int], bean=True):
    """Sync repository bean for ``Item`` (``bean=True`` marks the stereotype)."""


class AsyncItemRepo(AsyncCrudRepository[Item, int], bean=True):
    """Async repository bean for ``Item``."""
