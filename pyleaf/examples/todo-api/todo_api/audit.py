"""RequestAudit: the REQUEST-scoped per-request context bean.

The container injects it into singletons as a ``RequestScopedProxy``; the
real instance lives in the active ``RequestScope`` — one per HTTP request,
created on first access and destroyed (``@PreDestroy``) when the response
finishes.
"""

import uuid

from pyleaf.core import Component, RequestScope, Scope


@Component(scope=Scope.REQUEST)
class RequestAudit:
    """Per-request holder: an id plus the list of operations performed.

    ``RequestScope.current()`` in ``__init__`` makes the "only inside a
    request scope" invariant loud: any code path that instantiates the bean
    without an active scope (e.g. an endpoint enumerating *instances* rather
    than definitions) raises ``ScopeNotActive`` instead of silently leaking
    a request bean.
    """

    def __init__(self) -> None:
        RequestScope.current()
        self.request_id = uuid.uuid4().hex
        self.operations: list[str] = []

    def record(self, operation: str) -> None:
        """Append ``operation`` to this request's audit trail."""
        self.operations.append(operation)
