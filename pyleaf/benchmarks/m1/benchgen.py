"""Helpers for generating large synthetic bean graphs for M1 benchmarks."""

from __future__ import annotations

from pyleaf.core import Component

MODULE = "benchgen"


def make_component(name: str, dep_type: type | None = None, *, lazy: bool = True) -> type:
    """Create a ``@Component`` class whose constructor injects ``dep_type``."""
    if dep_type is None:

        def __init__(self) -> None:
            pass

    else:

        def __init__(self, dep) -> None:
            self.dep = dep

        __init__.__annotations__ = {"dep": dep_type, "return": None}

    cls = type(name, (), {"__init__": __init__, "__module__": MODULE})
    return Component(cls, lazy=lazy)


def make_chain_classes(count: int, *, lazy: bool = True, prefix: str = "Bench") -> list[type]:
    """Generate ``count`` component classes in ~3-deep dependency chains.

    Each chain is ``L0 -> L1 -> L2``; a trailing partial chain (1 or 2
    classes) fills the remainder so the total is exactly ``count``.
    """
    classes: list[type] = []
    chain = 0
    while len(classes) < count:
        prev: type | None = None
        for level in range(3):
            if len(classes) >= count:
                break
            cls = make_component(f"{prefix}L{level}_{chain}", prev, lazy=lazy)
            classes.append(cls)
            prev = cls
        chain += 1
    return classes
