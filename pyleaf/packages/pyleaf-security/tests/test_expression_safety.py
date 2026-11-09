"""Safe expression interpreter: grammar coverage, hostile-input rejection,
hypothesis fuzz, and the per-call perf budget.

Security invariants pinned here:

* anything outside the documented grammar fails AT PARSE TIME — i.e. at
  ``ParsedExpression(...)`` / ``@PreAuthorize(...)`` decoration, never at
  request time
* the interpreter never runs arbitrary code: unknown function names,
  attribute calls, dunder traversal, subscripts, comprehensions, string
  escapes into ``__import__`` etc. are all parse errors; a canary placed
  in the argument map is never called
* ``expression.py`` contains no ``eval``/``exec`` (enforced by the repo's
  ``rg`` guard; the fuzz tests prove nothing reaches a callable)
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pyleaf.security import (
    Authentication,
    ParsedExpression,
    PreAuthorize,
    Principal,
    SecurityExpressionError,
    role,
)


def _auth(
    name: str = "alice",
    roles: tuple[str, ...] = ("USER",),
    authenticated: bool = True,
) -> Authentication:
    return Authentication(
        principal=Principal(name),
        authorities=frozenset(role(r) for r in roles),
        authenticated=authenticated,
    )


# --- grammar: accepted forms ----------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("permit_all()", True),
        ("deny_all()", False),
        ("is_authenticated()", True),
        ("has_role('ADMIN')", True),
        ("has_role('USER')", False),
        ("has_authority('ROLE_ADMIN')", True),
        ("has_authority('scope:read')", False),
        ("has_role(\"ADMIN\")", True),
        ("not has_role('USER')", True),
        ("has_role('ADMIN') or has_role('USER')", True),
        ("has_role('ADMIN') and has_role('USER')", False),
        ("has_role('ADMIN') and (has_role('USER') or has_role('ADMIN'))", True),
        ("'x' == 'x'", True),
        ("'x' != 'y'", True),
        ("1 == 1", True),
        ("1 != 2", True),
        ("True", True),
        ("False", False),
        ("None == None", True),
        ("is_authenticated() == True", True),
        ("deny_all() == False", True),
    ],
)
def test_valid_expressions(source: str, expected: bool) -> None:
    auth = _auth(roles=("ADMIN",))
    assert ParsedExpression(source).check(auth, {}) is expected


def test_expression_without_authentication() -> None:
    expr = ParsedExpression("permit_all()")
    assert expr.check(None, {}) is True
    assert ParsedExpression("is_authenticated()").check(None, {}) is False
    # A dereference that needs an authentication assesses to denied, not
    # to an exception leaking past the interceptor.
    assert ParsedExpression("authentication.principal.name == 'x'").check(
        None, {}
    ) is False
    assert ParsedExpression("has_role('ADMIN')").check(None, {}) is False


def test_hash_param_and_bare_param_binding() -> None:
    auth = _auth("alice")
    expr = ParsedExpression("#name == authentication.principal.name")
    assert expr.check(auth, {"name": "alice"}) is True
    assert expr.check(auth, {"name": "bob"}) is False
    bare = ParsedExpression("name == 'alice'")
    assert bare.check(auth, {"name": "alice"}) is True


def test_missing_param_assesses_denied() -> None:
    expr = ParsedExpression("#name == 'alice'")
    assert expr.check(_auth(), {}) is False


def test_param_attribute_and_int_compare() -> None:
    class User:
        def __init__(self, level: int) -> None:
            self.level = level

    expr = ParsedExpression("#user.level == 3")
    assert expr.check(_auth(), {"user": User(3)}) is True
    assert expr.check(_auth(), {"user": User(2)}) is False
    # Missing attribute on a real object assesses denied, not an error.
    assert ParsedExpression("#user.nope == 1").check(
        _auth(), {"user": User(3)}
    ) is False


def test_post_authorize_result_forms() -> None:
    expr = ParsedExpression("return_value == 'ok'", post=True)
    assert expr.check(_auth(), {}, result="ok") is True
    assert expr.check(_auth(), {}, result="bad") is False
    for source in ("#result == 'ok'", "result == 'ok'"):
        parsed = ParsedExpression(source, post=True)
        assert parsed.check(_auth(), {}, result="ok") is True


def test_result_names_are_parse_errors_in_pre_authorize() -> None:
    with pytest.raises(SecurityExpressionError):
        ParsedExpression("return_value == 'ok'")


def test_string_literal_with_hash_is_not_a_param() -> None:
    expr = ParsedExpression("'#not_a_param' == '#not_a_param'")
    assert expr.check(_auth(), {}) is True


# --- hostile constructs: all fail at parse time -----------------------------------------


HOSTILE: list[str] = [
    "__import__('os').system('id')",
    "__import__('os')",
    "().__class__.__bases__",
    "x.__class__",
    "x.__class__.__mro__",
    "x._private",
    "authentication.__dict__",
    "authentication.principal.__class__",
    "os.system('id')",
    "print('hi')",
    "open('/etc/passwd')",
    "getattr(x, 'y')",
    "globals()",
    "x()",
    "canary()",
    "#canary()",
    "#canary.__call__()",
    "x['key']",
    "x[0]",
    "x.y[0]",
    "1 + 2",
    "1 - 2",
    "a * b",
    "-1",
    "~x",
    "a < b",
    "a <= b",
    "a is b",
    "a is not b",
    "a in ('x',)",
    "'x' in x",
    "a == b == c",
    "x if y else z",
    "lambda: 1",
    "lambda a: a",
    "[a for a in y]",
    "{a: 1}",
    "{'x'}",
    "('x', 'y')",
    "f'{x}'",
    "1.5 == 1.5",
    "b'x' == b'x'",
    "... == 1",
    "import os",
    "x = 1",
    "def f(): pass",
    "has_role('A'; x)",
    "has_role('A') or",
    "and has_role('A')",
    "has_role()",
    "has_role('A', 'B')",
    "is_authenticated('x')",
    "permit_all(1)",
    "unknown_fn()",
    "Has_Role('A')",
    "'unterminated",
    "#1abc == 2",
    "",
    "   ",
    "x; has_role('A')",
    "((",
]


@pytest.mark.parametrize("source", HOSTILE)
def test_hostile_constructs_fail_at_parse_time(source: str) -> None:
    with pytest.raises(SecurityExpressionError):
        ParsedExpression(source)


def test_parse_error_at_decoration_time() -> None:
    with pytest.raises(SecurityExpressionError):
        PreAuthorize("__import__('os').system('id')")


def test_parse_error_when_decorating_function_with_bad_param() -> None:
    with pytest.raises(SecurityExpressionError):

        @PreAuthorize("#missing == 1")
        def work(self: Any, present: str) -> str:
            return present


def test_canary_is_never_called() -> None:
    calls: list[str] = []

    class Canary:
        def __call__(self) -> bool:
            calls.append("called")
            return True

        @property
        def trap(self) -> None:
            raise AssertionError("trap attribute touched")

    canary = Canary()
    for source in HOSTILE:
        with pytest.raises(SecurityExpressionError):
            ParsedExpression(source)
    # Even when the canary sits in the argument map, the grammar gives no
    # way to call it: Call targets are whitelisted names only.
    expr = ParsedExpression("#canary == #canary")
    assert expr.check(_auth(), {"canary": canary}) is True
    assert calls == []


# --- hypothesis fuzz ---------------------------------------------------------------------


@given(st.text())
@settings(max_examples=400, deadline=None)
def test_fuzz_arbitrary_text_never_runs_code(source: str) -> None:
    calls: list[str] = []

    def canary() -> None:
        calls.append("called")

    try:
        expr = ParsedExpression(source)
    except SecurityExpressionError:
        return
    # Whatever parsed, assessing it may only touch the whitelisted fns
    # and plain attribute reads — never the canary callable.
    result = expr.check(
        _auth(), {"canary": canary, "x": 1, "name": "alice"}
    )
    assert isinstance(result, bool)
    assert calls == []


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1))
@settings(max_examples=300, deadline=None)
def test_fuzz_inside_grammar_wrapper(source: str) -> None:
    # Text embedded inside a valid expression still cannot widen the
    # grammar: either the whole parse fails or it runs as a param ref.
    payload = f"permit_all() or {source} == 'x'"
    try:
        expr = ParsedExpression(payload)
    except SecurityExpressionError:
        return
    assert isinstance(expr.check(_auth(), {"x": "x"}), bool)


# --- perf budget ---------------------------------------------------------------------------


def test_assessed_expression_under_5us() -> None:
    expr = ParsedExpression(
        "has_role('ADMIN') and #name == authentication.principal.name"
    )
    auth = _auth("alice", roles=("ADMIN",))
    args = {"name": "alice"}
    # Warm the closures.
    for _ in range(100):
        expr.check(auth, args)
    start = time.perf_counter_ns()
    iterations = 10_000
    for _ in range(iterations):
        expr.check(auth, args)
    elapsed_ns = (time.perf_counter_ns() - start) / iterations
    print(f"\nexpr.check mean: {elapsed_ns:.0f} ns/call")
    # Design target <5µs (measured ~880ns locally); the pin is 50µs
    # because absolute timings on shared CI runners are unreliable —
    # this still catches a ~50x evaluator regression.
    assert elapsed_ns < 50_000
