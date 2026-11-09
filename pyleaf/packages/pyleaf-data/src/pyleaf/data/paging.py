"""Sort / Pageable / Page value types for pyleaf repositories (WI 3.5, PYL-36).

Sort fields are *model attribute names*, never raw SQL: binding a
:class:`Sort` to a model validates every field against the mapped
``ColumnProperty`` attributes — an unknown name raises ``ValueError``
rather than being interpolated into a statement.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import ColumnProperty, Mapper

T = TypeVar("T")
U = TypeVar("U")


def _column_attr(model: type[Any], name: str) -> Any:
    """Resolve ``name`` to a mapped column attribute on ``model``.

    Raises ``ValueError`` when ``model`` is not a mapped class or ``name``
    is not a mapped ``ColumnProperty`` — sorting by anything else
    (relationships, hybrid attributes, raw strings) is rejected here so a
    bad field can never reach ``ORDER BY``.
    """
    inspected = sa_inspect(model)
    if not isinstance(inspected, Mapper):
        raise ValueError(f"{model!r} is not a mapped class — cannot sort")
    prop = inspected.column_attrs.get(name)
    if prop is None or not isinstance(prop, ColumnProperty):
        valid = ", ".join(sorted(inspected.column_attrs.keys()))
        raise ValueError(
            f"Unknown sort field '{name}' for"
            f" {model.__name__} — mapped column attributes: {valid}"
        )
    return getattr(model, name)


def _parse_order(spec: str) -> Order:
    """Parse one ``"-name"`` / ``"+name"`` / ``"name"`` order token."""
    token = spec.strip()
    if not token:
        raise ValueError(f"Empty sort field in {spec!r}")
    if token.startswith("-"):
        return Order(token[1:], descending=True)
    if token.startswith("+"):
        return Order(token[1:])
    return Order(token)


@dataclass(frozen=True)
class Order:
    """A single ordering over one mapped attribute name."""

    field: str
    descending: bool = False

    def to_clause(self, model: type[Any]) -> Any:
        """Bind to ``model``; ``ValueError`` when ``field`` is not mapped."""
        attr = _column_attr(model, self.field)
        return attr.desc() if self.descending else attr.asc()


class Sort:
    """An ordered tuple of :class:`Order`s; empty means unsorted.

    Constructors: ``Sort(Order("a"), Order("b", descending=True))``,
    ``Sort.asc("a", "b")``, ``Sort.desc("x")``, the comma-string form
    ``Sort.by("name,-id")`` ('``-``' = desc, '``+``' or bare = asc), and
    ``Sort.unsorted()``.
    """

    __slots__ = ("_orders",)

    def __init__(self, *orders: Order | str) -> None:
        parsed: list[Order] = []
        for order in orders:
            if isinstance(order, Order):
                parsed.append(order)
            elif isinstance(order, str):
                parsed.extend(_parse_order(part) for part in order.split(","))
            else:
                raise TypeError(f"Sort orders must be Order or str, got {order!r}")
        self._orders = tuple(parsed)

    @classmethod
    def by(cls, spec: str) -> Sort:
        """Comma-separated form: ``Sort.by("name,-id")``."""
        return cls(spec)

    @classmethod
    def asc(cls, *fields: str) -> Sort:
        """Ascending sort over ``fields``, in argument order."""
        return cls(*(Order(f) for f in fields))

    @classmethod
    def desc(cls, *fields: str) -> Sort:
        """Descending sort over ``fields``, in argument order."""
        return cls(*(Order(f, descending=True) for f in fields))

    @classmethod
    def unsorted(cls) -> Sort:
        """The empty sort — no ``ORDER BY`` is emitted."""
        return cls()

    @property
    def orders(self) -> tuple[Order, ...]:
        """The orders, in the requested precedence."""
        return self._orders

    @property
    def is_sorted(self) -> bool:
        """True when at least one order is present."""
        return bool(self._orders)

    @property
    def is_unsorted(self) -> bool:
        """True when no orders are present."""
        return not self._orders

    def and_(self, other: Sort) -> Sort:
        """Concatenate two sorts; ``self``'s orders take precedence."""
        return Sort(*self._orders, *other._orders)

    def to_clauses(self, model: type[Any]) -> tuple[Any, ...]:
        """Bind every order to ``model``; ``ValueError`` on unknown fields."""
        return tuple(order.to_clause(model) for order in self._orders)

    def __iter__(self) -> Iterator[Order]:
        return iter(self._orders)

    def __len__(self) -> int:
        return len(self._orders)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Sort) and self._orders == other._orders

    def __repr__(self) -> str:
        return f"Sort({', '.join(map(repr, self._orders))})"


@dataclass(frozen=True)
class Pageable:
    """A 0-based page request: ``page`` index, ``size`` rows, optional sort."""

    page: int
    size: int
    sort: Sort = field(default_factory=Sort.unsorted)

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError(f"Pageable.size must be > 0, got {self.size}")
        if self.page < 0:
            raise ValueError(f"Pageable.page must be >= 0, got {self.page}")

    @property
    def offset(self) -> int:
        """Row offset of this page (``page * size``)."""
        return self.page * self.size

    @property
    def limit(self) -> int:
        """Rows per page (``size``)."""
        return self.size


@dataclass(frozen=True)
class Page(Generic[T]):
    """One slice of a paged result plus its navigation metadata."""

    content: Sequence[T]
    number: int
    size: int
    total_elements: int

    @property
    def total_pages(self) -> int:
        """Pages needed for ``total_elements`` at this ``size``."""
        return math.ceil(self.total_elements / self.size) if self.size else 0

    @property
    def has_next(self) -> bool:
        """True when a page after ``number`` exists."""
        return self.number < self.total_pages - 1

    @property
    def has_previous(self) -> bool:
        """True when ``number`` is not the first page."""
        return self.number > 0

    @property
    def first(self) -> bool:
        """True when this is page 0."""
        return self.number == 0

    @property
    def last(self) -> bool:
        """True when no further page exists (also for an empty result)."""
        return not self.has_next

    def map(self, fn: Callable[[T], U]) -> Page[U]:
        """Transform the content, keeping the page metadata."""
        return Page(
            content=[fn(item) for item in self.content],
            number=self.number,
            size=self.size,
            total_elements=self.total_elements,
        )

    def __iter__(self) -> Iterator[T]:
        return iter(self.content)

    def __len__(self) -> int:
        return len(self.content)
