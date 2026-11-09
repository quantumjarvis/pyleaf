"""Shared ORM model for the SA transaction-manager tests."""

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the SA tx test tables."""


class TxItem(Base):
    """A single-row entity written inside transactions under test."""

    __tablename__ = "sa_tx_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
