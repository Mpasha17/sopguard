"""A tiny, safe expression language for policy conditions.

Policies are data, not code: expressions are parsed with :mod:`ast` and only a whitelisted
subset is evaluated (literals, names, attribute/subscript access on dicts, comparisons,
boolean/arithmetic operators and calls to registered helper functions). No imports, no
dunder access, no lambdas, no comprehensions.

Missing data is a first-class outcome: accessing an attribute of ``None`` raises
:class:`MissingFact`, which the supervisor turns into "insufficient context" instead of
silently passing a check.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any


class ExprError(ValueError):
    """The expression is not valid policy syntax (raised at compile time)."""


class MissingFact(LookupError):
    """The expression referenced state that does not exist yet (e.g. order not looked up)."""


_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_CMP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}
_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.BinOp, ast.Compare,
    ast.Call, ast.Name, ast.Load, ast.Attribute, ast.Subscript, ast.Constant, ast.List, ast.Tuple, ast.Dict,
    ast.IfExp, ast.keyword, *_BIN.keys(), *_CMP.keys(),
)  # fmt: skip


@lru_cache(maxsize=2048)
def parse(src: str) -> ast.Expression:
    try:
        tree = ast.parse(src.strip(), mode="eval")
    except SyntaxError as e:
        raise ExprError(f"invalid expression {src!r}: {e.msg}") from e
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ExprError(f"{type(node).__name__} is not allowed in policy expressions: {src!r}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExprError(f"private attribute access is not allowed: {src!r}")
        if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
            raise ExprError(f"only helper functions may be called: {src!r}")
    return tree


def names(src: str) -> set[str]:
    return {n.id for n in ast.walk(parse(src)) if isinstance(n, ast.Name)}


class Env:
    """Evaluation scope: plain values, lazily-evaluated ``let`` bindings and helper functions."""

    def __init__(
        self,
        values: Mapping[str, Any],
        helpers: Mapping[str, Callable[..., Any]],
        lets: Mapping[str, str] | None = None,
    ) -> None:
        self.values = dict(values)
        self.helpers = helpers
        self.lets = dict(lets or {})
        self._cache: dict[str, Any] = {}
        self._active: set[str] = set()

    def lookup(self, name: str) -> Any:
        if name in self.values:
            return self.values[name]
        if name in self.lets:
            if name in self._cache:
                return self._cache[name]
            if name in self._active:
                raise ExprError(f"recursive let binding {name!r}")
            self._active.add(name)
            try:
                val = evaluate(self.lets[name], self)
            finally:
                self._active.discard(name)
            self._cache[name] = val
            return val
        if name in ("true", "True"):
            return True
        if name in ("false", "False"):
            return False
        if name in ("null", "None"):
            return None
        raise ExprError(f"unknown name {name!r}")


def _get(obj: Any, key: Any, src: str) -> Any:
    if obj is None:
        raise MissingFact(f"{src}: cannot read {key!r} of nothing")
    if isinstance(obj, Mapping):
        if key not in obj:
            raise MissingFact(f"{src}: field {key!r} is missing")
        return obj[key]
    if isinstance(obj, (list, tuple, str)) and isinstance(key, int):
        try:
            return obj[key]
        except IndexError as e:
            raise MissingFact(f"{src}: index {key} out of range") from e
    raise MissingFact(f"{src}: cannot read {key!r} of {type(obj).__name__}")


def evaluate(src: str, env: Env) -> Any:
    return _ev(parse(src).body, env, src)


def _ev(node: ast.AST, env: Env, src: str) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.lookup(node.id)
    if isinstance(node, ast.Attribute):
        return _get(_ev(node.value, env, src), node.attr, src)
    if isinstance(node, ast.Subscript):
        return _get(_ev(node.value, env, src), _ev(node.slice, env, src), src)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_ev(e, env, src) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {_ev(k, env, src): _ev(v, env, src) for k, v in zip(node.keys, node.values, strict=True)}  # type: ignore[arg-type]
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            val: Any = True
            for v in node.values:
                val = _ev(v, env, src)
                if not val:
                    return val
            return val
        val = False
        for v in node.values:
            val = _ev(v, env, src)
            if val:
                return val
        return val
    if isinstance(node, ast.UnaryOp):
        v = _ev(node.operand, env, src)
        return (not v) if isinstance(node.op, ast.Not) else -v
    if isinstance(node, ast.BinOp):
        left, right = _ev(node.left, env, src), _ev(node.right, env, src)
        if left is None or right is None:
            raise MissingFact(f"{src}: arithmetic on a missing value")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.Compare):
        left = _ev(node.left, env, src)
        for op, comp in zip(node.ops, node.comparators, strict=True):
            right = _ev(comp, env, src)
            if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) and (left is None or right is None):
                raise MissingFact(f"{src}: comparison with a missing value")
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _ev(node.body if _ev(node.test, env, src) else node.orelse, env, src)
    if isinstance(node, ast.Call):
        fname = node.func.id  # type: ignore[attr-defined]
        fn = env.helpers.get(fname)
        if fn is None:
            raise ExprError(f"unknown helper {fname!r} in {src!r}")
        args = [_ev(a, env, src) for a in node.args]
        kwargs = {k.arg: _ev(k.value, env, src) for k in node.keywords if k.arg}
        return fn(*args, **kwargs)
    raise ExprError(f"unsupported node {type(node).__name__}")  # pragma: no cover
