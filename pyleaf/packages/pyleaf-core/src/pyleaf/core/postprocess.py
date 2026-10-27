"""Bean post-processors: interception hooks around bean initialization."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

BEFORE_INIT_HOOK = "post_process_before_init"
AFTER_INIT_HOOK = "post_process_after_init"


@runtime_checkable
class BeanPostProcessor(Protocol):
    """Structural type for bean post-processors.

    Both hooks are optional in practice: a processor may implement only one
    of them. Discovery is therefore duck-typed — the context checks each hook
    with ``callable(getattr(...))`` via :func:`is_bean_post_processor` rather
    than relying on ``isinstance`` against this Protocol, so a processor
    implementing a single hook is still found (an ``isinstance`` check would
    require both members to be present).
    """

    def post_process_before_init(self, bean: object, name: str) -> object:
        """Run before the bean's ``@PostConstruct``; may return a replacement."""
        ...

    def post_process_after_init(self, bean: object, name: str) -> object:
        """Run after the bean's ``@PostConstruct``; may return a replacement."""
        ...


def is_bean_post_processor(bean_type: Any) -> bool:
    """Return True when ``bean_type`` implements at least one BPP hook."""
    return callable(getattr(bean_type, BEFORE_INIT_HOOK, None)) or callable(
        getattr(bean_type, AFTER_INIT_HOOK, None)
    )
