"""Topological ordering of @AutoConfiguration classes."""

import pytest
from pyleaf.boot import (
    AutoConfiguration,
    AutoConfigureAfter,
    AutoConfigureBefore,
    AutoConfigureOrder,
    sort_autoconfigs,
)
from pyleaf.core import PyleafError


@AutoConfiguration
class ConfigA:
    pass


@AutoConfiguration
class ConfigB:
    pass


def test_after_places_dependency_first() -> None:
    @AutoConfigureAfter(ConfigB)
    @AutoConfiguration
    class Late:
        pass

    ordered = sort_autoconfigs([Late, ConfigB])

    assert ordered.index(ConfigB) < ordered.index(Late)


def test_before_places_dependent_last() -> None:
    @AutoConfigureBefore(ConfigA)
    @AutoConfiguration
    class Early:
        pass

    ordered = sort_autoconfigs([ConfigA, Early])

    assert ordered.index(Early) < ordered.index(ConfigA)


def test_order_breaks_ties_lower_first() -> None:
    @AutoConfigureOrder(5)
    @AutoConfiguration
    class Higher:
        pass

    @AutoConfiguration  # default order 0
    class Lower:
        pass

    assert sort_autoconfigs([Higher, Lower]) == [Lower, Higher]


def test_qualified_name_breaks_remaining_ties() -> None:
    # Same (default) order on both -> deterministic qualified-name order.
    assert sort_autoconfigs([ConfigB, ConfigA]) == [ConfigA, ConfigB]
    assert sort_autoconfigs([ConfigA, ConfigB]) == [ConfigA, ConfigB]


def test_string_refs_resolve_against_known_classes() -> None:
    @AutoConfigureAfter(f"{__name__}.ConfigB")
    @AutoConfiguration
    class ByModuleName:
        pass

    @AutoConfigureAfter("ConfigB")
    @AutoConfiguration
    class ByBareName:
        pass

    ordered = sort_autoconfigs([ByModuleName, ByBareName, ConfigB])

    assert ordered[0] is ConfigB


def test_unresolvable_ref_is_ignored() -> None:
    @AutoConfigureAfter("no.such.Class")
    @AutoConfiguration
    class Lone:
        pass

    assert sort_autoconfigs([Lone]) == [Lone]


def test_cycle_raises_pyleaf_error_naming_both() -> None:
    @AutoConfiguration
    class CycA:
        pass

    @AutoConfiguration
    class CycB:
        pass

    AutoConfigureAfter(CycB)(CycA)
    AutoConfigureAfter(CycA)(CycB)

    with pytest.raises(PyleafError) as exc:
        sort_autoconfigs([CycA, CycB])

    assert "CycA" in str(exc.value)
    assert "CycB" in str(exc.value)


def test_topo_sort_respects_multiple_edges() -> None:
    @AutoConfigureAfter(ConfigA)
    @AutoConfigureBefore(f"{__name__}.Sink")
    @AutoConfiguration
    class Middle:
        pass

    @AutoConfiguration
    class Sink:
        pass

    ordered = sort_autoconfigs([Sink, Middle, ConfigA])

    assert ordered.index(ConfigA) < ordered.index(Middle) < ordered.index(Sink)
