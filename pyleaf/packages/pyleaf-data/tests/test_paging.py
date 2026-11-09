"""Sort / Pageable / Page contract tests (WI 3.5)."""

import pytest
from data_models import Item, ItemRepo
from pyleaf.core import ApplicationContext
from pyleaf.data import Order, Page, Pageable, Sort
from pyleaf.starter.sqlalchemy.tx import SqlAlchemyTransactionManager
from pyleaf.tx import TransactionTemplate


def _tmpl(ctx: ApplicationContext) -> TransactionTemplate:
    return TransactionTemplate(ctx.get(SqlAlchemyTransactionManager))


def _seed(ctx: ApplicationContext) -> None:
    """7 rows with distinct prices, some shared names."""
    repo = ctx.get(ItemRepo)
    items = [
        Item(name="b", price=10),
        Item(name="a", price=20),
        Item(name="b", price=30),
        Item(name="a", price=40),
        Item(name="c", price=50),
        Item(name="a", price=60),
        Item(name="c", price=70),
    ]
    with _tmpl(ctx).required():
        repo.save_all(items)


# -- value-object units -------------------------------------------------------


def test_sort_builders() -> None:
    assert Sort.asc("name").orders == (Order("name"),)
    assert Sort.desc("x").orders == (Order("x", descending=True),)
    assert Sort.by("name,-id").orders == (
        Order("name"),
        Order("id", descending=True),
    )
    assert Sort.unsorted().is_unsorted
    assert not Sort.unsorted().is_sorted
    assert Sort("price,-name").orders == (
        Order("price"),
        Order("name", descending=True),
    )


def test_sort_and_concatenates() -> None:
    combined = Sort.asc("a").and_(Sort.desc("b"))
    assert combined.orders == (Order("a"), Order("b", descending=True))


def test_pageable_enforces_invariants() -> None:
    with pytest.raises(ValueError, match="size"):
        Pageable(0, 0)
    with pytest.raises(ValueError, match="page"):
        Pageable(-1, 5)
    p = Pageable(2, 10)
    assert p.offset == 20
    assert p.limit == 10


def test_page_flags_and_map() -> None:
    page: Page[int] = Page(content=[1, 2], number=1, size=2, total_elements=5)
    assert page.total_pages == 3
    assert page.has_next
    assert page.has_previous
    assert not page.first
    assert not page.last
    assert len(page) == 2
    assert list(page) == [1, 2]
    mapped = page.map(lambda x: f"n{x}")
    assert mapped.content == ["n1", "n2"]
    assert mapped.number == 1 and mapped.total_elements == 5


def test_unknown_sort_field_raises_value_error(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    _seed(ctx)
    with pytest.raises(ValueError, match="Unknown sort field 'bogus'"), (
        _tmpl(ctx).required()
    ):
        repo.find_all(sort=Sort.asc("bogus"))
    with pytest.raises(ValueError, match="Unknown sort field"), (
        _tmpl(ctx).required()
    ):
        repo.find_all(pageable=Pageable(0, 5, Sort.by("id,-nope")))


# -- repository integration ---------------------------------------------------


def test_find_all_sort_desc(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    _seed(ctx)
    with _tmpl(ctx).required():
        prices = [i.price for i in repo.find_all(sort=Sort.desc("price"))]
        assert prices == [70, 60, 50, 40, 30, 20, 10]


def test_find_all_sort_multi(ctx: ApplicationContext) -> None:
    """name asc then id desc within equal names."""
    repo = ctx.get(ItemRepo)
    _seed(ctx)
    with _tmpl(ctx).required():
        rows = [(i.name, i.price) for i in repo.find_all(sort=Sort.by("name,-id"))]
        names = [r[0] for r in rows]
        assert names == sorted(names)
        # the three 'a's have prices 60,40,20 -> ids 6,4,2 -> desc id order
        a_prices = [r[1] for r in rows if r[0] == "a"]
        assert a_prices == [60, 40, 20]


def test_find_all_pageable_returns_page(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    _seed(ctx)
    with _tmpl(ctx).required():
        page = repo.find_all(
            pageable=Pageable(1, 3, sort=Sort.asc("price"))
        )
        assert isinstance(page, Page)
        assert [i.price for i in page.content] == [40, 50, 60]
        assert page.number == 1
        assert page.size == 3
        assert page.total_elements == 7
        assert page.total_pages == 3
        assert page.has_next
        assert page.has_previous
        assert not page.first
        assert not page.last


def test_find_all_pageable_first_and_last_page(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    _seed(ctx)
    with _tmpl(ctx).required():
        first = repo.find_all(pageable=Pageable(0, 3, Sort.asc("price")))
        assert first.first
        assert not first.has_previous
        assert [i.price for i in first.content] == [10, 20, 30]
        last = repo.find_all(pageable=Pageable(2, 3, Sort.asc("price")))
        assert last.last
        assert not last.has_next
        assert [i.price for i in last.content] == [70]


def test_find_all_pageable_beyond_end_is_empty(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    _seed(ctx)
    with _tmpl(ctx).required():
        page = repo.find_all(pageable=Pageable(5, 3))
        assert page.content == []
        assert page.total_elements == 7
        assert page.total_pages == 3


def test_find_all_pageable_empty_table(ctx: ApplicationContext) -> None:
    repo = ctx.get(ItemRepo)
    with _tmpl(ctx).required():
        page = repo.find_all(pageable=Pageable(0, 10))
        assert page.content == []
        assert page.total_elements == 0
        assert page.total_pages == 0
        assert page.first
        assert page.last


def test_find_all_sort_arg_applies_with_pageable(ctx: ApplicationContext) -> None:
    """An explicit ``sort`` composes with the pageable's own sort."""
    repo = ctx.get(ItemRepo)
    _seed(ctx)
    with _tmpl(ctx).required():
        page = repo.find_all(
            sort=Sort.asc("price"), pageable=Pageable(0, 2)
        )
        assert [i.price for i in page.content] == [10, 20]
