"""Testing support for pyleaf applications.

Public API: the ``@PyleafTest`` decorator and the ``mock_bean`` /
``spy_bean`` override descriptors. The ``pyleaf_context`` and
``pyleaf_beans`` fixtures are provided by the ``pyleaf.test.plugin``
pytest plugin (registered via the ``pyleaf`` ``pytest11`` entry point) —
request them by name; do not import them.
"""

from .plugin import BeanOverride, PyleafTest, mock_bean, spy_bean

__all__ = [
    "BeanOverride",
    "PyleafTest",
    "mock_bean",
    "spy_bean",
]
