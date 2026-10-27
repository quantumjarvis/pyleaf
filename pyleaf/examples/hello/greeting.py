"""GreetingService: reads ``app.greeting`` via Environment injection."""

from pyleaf.core import Environment, Service


@Service
class GreetingService:
    """Example component; ``public``/``internal`` prove self-invocation AOP."""

    def __init__(self, environment: Environment) -> None:
        self._environment = environment

    def greet(self) -> str:
        """Return the configured greeting."""
        return str(self._environment.get("app.greeting", default="Hello!"))

    def public(self) -> str:
        """Call ``self.internal()`` — the self-invocation is intercepted."""
        return "public->" + self.internal()

    def internal(self) -> str:
        """Interception target for the counting aspect."""
        return "internal"
