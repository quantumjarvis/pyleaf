"""ORM model for the scanned-app fixture (WI 3.6 e2e)."""

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the fixture app tables."""


class Note(Base):
    """Entity written through the scanned repository bean."""

    __tablename__ = "fixture_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(128))
