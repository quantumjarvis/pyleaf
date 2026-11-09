"""ApplicationContext builders for the pyleaf-data tests.

Repositories run as real beans: the config registers the session factory
and the starter's transaction manager as ``@Bean``s, the repos as
stereotype components (``bean=True`` on the class base), so
``ctx.get(ItemRepo)`` resolves a fully injected repository whose ops
bind the ambient transaction via the TSM.
"""

from typing import Any

from data_models import AsyncItemRepo, ItemRepo
from pyleaf.core import ApplicationContext, Bean, Configuration
from pyleaf.starter.sqlalchemy.tx import (
    AsyncSqlAlchemyTransactionManager,
    SqlAlchemyTransactionManager,
)
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import sessionmaker


def build_sync_ctx(
    sm: sessionmaker,  # type: ignore[type-arg]
    extra_repos: tuple[type[Any], ...] = (),
) -> ApplicationContext:
    """A refreshed context: sessionmaker + sync tx manager + repo beans."""
    ctx = ApplicationContext()

    @Configuration
    class _DataConfig:
        @Bean
        def sessionfactory(self) -> sessionmaker:  # type: ignore[type-arg]
            return sm

        @Bean
        def txManager(
            self,
            sessionfactory: sessionmaker,  # type: ignore[type-arg]
        ) -> SqlAlchemyTransactionManager:
            return SqlAlchemyTransactionManager(sessionfactory)

    ctx.register(_DataConfig)
    ctx.register(ItemRepo)
    for repo in extra_repos:
        ctx.register(repo)
    ctx.refresh()
    return ctx


def build_async_ctx(
    sm: async_sessionmaker,  # type: ignore[type-arg]
    extra_repos: tuple[type[Any], ...] = (),
) -> ApplicationContext:
    """A refreshed context: async sessionmaker + async tx manager + repo."""
    ctx = ApplicationContext()

    @Configuration
    class _AsyncDataConfig:
        @Bean
        def sessionfactory(self) -> async_sessionmaker:  # type: ignore[type-arg]
            return sm

        @Bean
        def txManager(
            self,
            sessionfactory: async_sessionmaker,  # type: ignore[type-arg]
        ) -> AsyncSqlAlchemyTransactionManager:
            return AsyncSqlAlchemyTransactionManager(sessionfactory)

    ctx.register(_AsyncDataConfig)
    ctx.register(AsyncItemRepo)
    for repo in extra_repos:
        ctx.register(repo)
    ctx.refresh()
    return ctx
