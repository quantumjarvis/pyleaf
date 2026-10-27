from __future__ import annotations

from typing import Annotated

from pyleaf.core import Component, Qualifier


@Component
class Dep:
    pass

@Component("namedDep")
class NamedDep(Dep):
    pass

@Component
class UsesDep:
    def __init__(self, dep: Dep):  # Dep resolves via module globals (postponed annotations)
        self.dep = dep

@Component
class UsesNamed:
    def __init__(self, dep: Annotated[Dep, Qualifier("namedDep")]):
        self.dep = dep

@Component
class UsesList:
    def __init__(self, deps: list[Dep]):
        self.deps = deps

@Component
class UsesOptional:
    def __init__(self, dep: OtherDep | None):  # OtherDep NOT a component -> None
        self.dep = dep

class OtherDep:  # deliberately not a component
    pass