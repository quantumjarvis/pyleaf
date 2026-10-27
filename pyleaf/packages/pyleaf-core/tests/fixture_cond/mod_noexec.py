"""Module that must never be executed by condition evaluation.

@ConditionalOnClass checks importability via ``find_spec``, which must not
run module code. If this module is ever imported, the import itself blows up.
"""

raise RuntimeError("fixture_cond.mod_noexec must never be imported")
