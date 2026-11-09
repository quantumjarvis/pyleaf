"""Typed repositories, @Query statements and paging for pyleaf (WI 3.5, PYL-36),
plus the WI 3.6 data auto-configuration pieces."""

from .autoconfig import (
    ENABLE_TX_ATTR,
    DataAutoConfiguration,
    EnableTransactionManagement,
    TransactionManagementMarker,
)
from .migrations import AlembicMigrationRunner, DataMigrationProperties
from .paging import Order, Page, Pageable, Sort
from .query import Query, ReturnHint
from .repository import AsyncCrudRepository, CrudRepository

__all__ = [
    "ENABLE_TX_ATTR",
    "AlembicMigrationRunner",
    "AsyncCrudRepository",
    "CrudRepository",
    "DataAutoConfiguration",
    "DataMigrationProperties",
    "EnableTransactionManagement",
    "Order",
    "Page",
    "Pageable",
    "Query",
    "ReturnHint",
    "Sort",
    "TransactionManagementMarker",
]
