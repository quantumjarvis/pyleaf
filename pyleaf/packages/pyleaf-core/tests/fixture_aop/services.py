"""Fixture components for the AOP pointcut tests.

Beans here live under the ``fixture_aop`` package prefix so that
``within("fixture_aop")`` matches them, while classes defined inside the
test modules themselves do not match.
"""

from pyleaf.core import Component


@Component
class TargetService:
    """Primary interception target for the pointcut truth table."""

    def work(self) -> str:
        return "worked"

    def find_one(self) -> str:
        return "one"

    def find_all(self) -> list[str]:
        return ["all"]

    def helper(self) -> str:
        return "helped"


@Component
class PeerService:
    """Same module prefix as TargetService but unrelated by type."""

    def work(self) -> str:
        return "peer"

    def find_peer(self) -> str:
        return "peer-found"
