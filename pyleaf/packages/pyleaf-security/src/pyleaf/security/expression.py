"""Safe whitelist interpreter for method-security expressions.

``@PreAuthorize``/``@PostAuthorize`` strings are parsed ONCE — at
decoration for method-level marks, at refresh for class-level ones —
into a closure tree by :class:`ParsedExpression`. Anything outside the
grammar raises :class:`SecurityExpressionError` AT PARSE TIME, so a bad
expression fails at bean creation, never at request time. The source is
parsed with :func:`ast.parse` and walked; this module contains no
dynamic-code machinery at all (a ``rg`` grep guard over this file pins
that invariant).

Grammar
-------
* functions (the only calls): ``has_role('X')``, ``has_authority('X')``,
  ``is_authenticated()``, ``permit_all()``, ``deny_all()``
* names: ``authentication``, ``principal``; ``#param`` and bare ``param``
  for the bound method arguments; ``return_value``, ``result`` and
  ``#result`` for the return value (post-authorize only)
* attribute reads: ``authentication.principal.name`` — public attributes
  only (no ``_``-prefixed names) on a name/parameter root
* operators: ``and``, ``or``, ``not``, one ``==``/``!=`` comparison,
  parentheses
* literals: single/double-quoted strings, ints, ``True``/``False``/``None``

Anything else — unknown function calls (including calls on parameters or
attributes), subscripts, comprehensions, lambdas, arithmetic, chained or
non-equality comparisons, floats/bytes/f-strings, statements — is a
parse-time error. Missing data (no authentication, absent argument or
attribute) reads as *denied*, never as an escape hatch.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from pyleaf.core.errors import PyleafError

if TYPE_CHECKING:
    from .core import Authentication


class SecurityExpressionError(PyleafError):
    """A security expression is outside the supported grammar."""


class _Missing(Exception):
    """Internal control flow: a reference needed data that is absent."""


#: ``#param`` spellings are rewritten to this identifier prefix pre-parse.
_ARG_PREFIX = "__p_arg_"

#: Whitelisted function name -> required arity.
_FN_ARITY: dict[str, int] = {
    "has_role": 1,
    "has_authority": 1,
    "is_authenticated": 0,
    "permit_all": 0,
    "deny_all": 0,
}


class _Scope:
    """What one assessment can see: the auth, bound args, and result."""

    __slots__ = ("args", "authentication", "result")

    def __init__(
        self,
        authentication: Authentication | None,
        args: Mapping[str, Any],
        result: Any = None,
    ) -> None:
        self.authentication = authentication
        self.args = args
        self.result = result


Getter = Callable[[_Scope], Any]


def _fn_has_role(scope: _Scope, name: Any) -> bool:
    auth = scope.authentication
    return auth is not None and isinstance(name, str) and auth.has_role(name)


def _fn_has_authority(scope: _Scope, name: Any) -> bool:
    auth = scope.authentication
    return (
        auth is not None
        and isinstance(name, str)
        and auth.has_authority(name)
    )


def _fn_is_authenticated(scope: _Scope) -> bool:
    auth = scope.authentication
    return auth is not None and auth.authenticated


def _fn_permit_all(_scope: _Scope) -> bool:
    return True


def _fn_deny_all(_scope: _Scope) -> bool:
    return False


_FN_IMPLS: dict[str, Callable[..., bool]] = {
    "has_role": _fn_has_role,
    "has_authority": _fn_has_authority,
    "is_authenticated": _fn_is_authenticated,
    "permit_all": _fn_permit_all,
    "deny_all": _fn_deny_all,
}


def _read_principal(scope: _Scope) -> Any:
    auth = scope.authentication
    if auth is None:
        raise _Missing()
    return auth.principal


def _arg_getter(name: str) -> Getter:
    def read(scope: _Scope) -> Any:
        try:
            return scope.args[name]
        except KeyError:
            raise _Missing() from None

    return read


def _translate(source: str) -> str:
    """Rewrite ``#name`` to ``__p_arg_name`` outside string literals.

    ``#`` is Python's comment marker, so the rewrite happens on the raw
    source before :func:`ast.parse`. A ``#`` not followed by an
    identifier start is left in place — what follows either comments out
    harmlessly or trips the syntax error below.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch in ("'", '"'):
            # Copy the quoted literal verbatim (escapes honoured), so a
            # '#' inside a string stays a literal character.
            j = i + 1
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == ch:
                    break
                j += 1
            end = j + 1 if j < n else n
            out.append(source[i:end])
            i = end
        elif ch == "#":
            j = i + 1
            while j < n and (source[j] == "_" or source[j].isalnum()):
                j += 1
            name = source[i + 1 : j]
            if name and (name[0] == "_" or name[0].isalpha()):
                out.append(_ARG_PREFIX + name)
                i = j
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class ParsedExpression:
    """A security expression parsed once into a closed-over getter tree.

    Construction performs the full whitelist walk — unknown grammar fails
    HERE, not at call time. :meth:`check` then runs the compiled getters:
    short-circuiting ``and``/``or``, single ``==``/``!=`` comparisons,
    whitelisted function calls, name/attribute/literal reads.
    """

    __slots__ = ("_fn", "_params", "post", "source")

    def __init__(self, source: str, *, post: bool = False) -> None:
        self.source = source
        self.post = post
        self._params: set[str] = set()
        translated = _translate(source)
        try:
            module = ast.parse(translated)
        except SyntaxError as exc:
            raise SecurityExpressionError(
                f"Invalid security expression {source!r}: {exc}"
            ) from exc
        if len(module.body) != 1 or not isinstance(module.body[0], ast.Expr):
            raise SecurityExpressionError(
                f"Invalid security expression {source!r}:"
                " a single expression is required"
            )
        self._fn = self._compile(module.body[0].value)

    @property
    def params(self) -> frozenset[str]:
        """Method-parameter names the expression references."""
        return frozenset(self._params)

    def check(
        self,
        authentication: Authentication | None,
        args: Mapping[str, Any],
        result: Any = None,
    ) -> bool:
        """Assess the expression; missing data reads as denied."""
        scope = _Scope(authentication, args, result)
        try:
            return bool(self._fn(scope))
        except (_Missing, AttributeError):
            return False

    def validate_params(self, func: Callable[..., Any], owner: str) -> None:
        """Fail when the expression references parameters ``func`` lacks.

        Run at decoration for function targets and per method at refresh
        for class-level marks — either way before the first call. A
        ``**kwargs`` signature accepts any name.
        """
        if not self._params:
            return
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return
        if any(
            p.kind is inspect.Parameter.VAR_KEYWORD
            for p in signature.parameters.values()
        ):
            return
        declared = set(signature.parameters)
        unknown = sorted(self._params - declared)
        if unknown:
            raise SecurityExpressionError(
                f"{owner}: expression {self.source!r} references unknown"
                f" parameter(s) {', '.join(unknown)}"
            )

    # -- compile-time whitelist walk ----------------------------------------

    def _unsupported(self, node: ast.AST, what: str = "construct") -> SecurityExpressionError:
        return SecurityExpressionError(
            f"Expression {self.source!r}: unsupported {what}"
            f" '{type(node).__name__}' — the grammar admits and/or/not,"
            " a single ==/!=, whitelisted functions, names, public"
            " attributes and literals only"
        )

    def _compile(self, node: ast.AST) -> Getter:
        if isinstance(node, ast.Expression):
            return self._compile(node.body)
        if isinstance(node, ast.BoolOp):
            fns = [self._compile(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return lambda s: all(f(s) for f in fns)
            if isinstance(node.op, ast.Or):
                return lambda s: any(f(s) for f in fns)
            raise self._unsupported(node, "boolean operator")
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, ast.Not):
                raise self._unsupported(node, "unary operator")
            inner = self._compile(node.operand)
            return lambda s: not inner(s)
        if isinstance(node, ast.Compare):
            return self._compile_compare(node)
        if isinstance(node, ast.Call):
            return self._compile_call(node)
        if isinstance(node, ast.Attribute):
            return self._compile_attribute(node)
        if isinstance(node, ast.Name):
            return self._compile_name(node.id)
        if isinstance(node, ast.Constant):
            return self._compile_constant(node)
        raise self._unsupported(node)

    def _compile_compare(self, node: ast.Compare) -> Getter:
        if len(node.ops) != 1 or not isinstance(
            node.ops[0], (ast.Eq, ast.NotEq)
        ):
            raise SecurityExpressionError(
                f"Expression {self.source!r}: only a single '==' or '!='"
                " comparison is supported"
            )
        left = self._compile(node.left)
        right = self._compile(node.comparators[0])
        if isinstance(node.ops[0], ast.Eq):
            return lambda s: left(s) == right(s)
        return lambda s: left(s) != right(s)

    def _compile_call(self, node: ast.Call) -> Getter:
        func = node.func
        if not isinstance(func, ast.Name) or func.id not in _FN_ARITY:
            raise SecurityExpressionError(
                f"Expression {self.source!r}: only calls to"
                f" {sorted(_FN_ARITY)} are allowed"
            )
        if node.keywords:
            raise SecurityExpressionError(
                f"Expression {self.source!r}: keyword arguments"
                " are not allowed"
            )
        name = func.id
        if len(node.args) != _FN_ARITY[name]:
            raise SecurityExpressionError(
                f"Expression {self.source!r}: {name}() takes"
                f" {_FN_ARITY[name]} argument(s)"
            )
        impl = _FN_IMPLS[name]
        arg_getters = [self._compile(a) for a in node.args]
        return lambda s: impl(s, *[g(s) for g in arg_getters])

    def _compile_attribute(self, node: ast.Attribute) -> Getter:
        if node.attr.startswith("_"):
            raise SecurityExpressionError(
                f"Expression {self.source!r}: private/dunder attribute"
                f" '{node.attr}' is not allowed"
            )
        if not isinstance(node.value, (ast.Name, ast.Attribute)):
            raise SecurityExpressionError(
                f"Expression {self.source!r}: attribute base must be"
                " a name"
            )
        base = self._compile(node.value)
        attr = node.attr

        def read(scope: _Scope) -> Any:
            obj = base(scope)
            if obj is None:
                raise _Missing()
            return getattr(obj, attr)

        return read

    def _compile_name(self, name: str) -> Getter:
        if name == "authentication":
            return lambda s: s.authentication
        if name == "principal":
            return _read_principal
        if name == "return_value":
            if not self.post:
                raise SecurityExpressionError(
                    f"Expression {self.source!r}: 'return_value' is only"
                    " available in @PostAuthorize expressions"
                )
            return lambda s: s.result
        if name == "result" and self.post:
            return lambda s: s.result
        if name.startswith(_ARG_PREFIX):
            param = name[len(_ARG_PREFIX) :]
            if param == "result" and self.post:
                return lambda s: s.result
            self._params.add(param)
            return _arg_getter(param)
        self._params.add(name)
        return _arg_getter(name)

    def _compile_constant(self, node: ast.Constant) -> Getter:
        value = node.value
        if value is None or isinstance(value, (bool, str, int)):
            return lambda s: value
        raise SecurityExpressionError(
            f"Expression {self.source!r}: literal {value!r} is outside"
            " the grammar (strings, ints, True/False/None only)"
        )


__all__ = ["ParsedExpression", "SecurityExpressionError"]
