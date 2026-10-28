"""Exception hierarchy for pyleaf-core."""


class PyleafError(Exception):
    """Base class for all pyleaf errors."""


class NoSuchBean(PyleafError):
    """Raised when no bean satisfies a required type or name."""


class NoUniqueBean(PyleafError):
    """Raised when more than one bean satisfies a required type."""


class CircularDependency(PyleafError):
    """Raised when the dependency graph contains a cycle."""


class BeanCreationError(PyleafError):
    """Raised when a bean cannot be instantiated; the original error is chained."""


class ScopeNotActive(PyleafError):
    """Raised when a request-scoped bean is touched outside an active scope."""
