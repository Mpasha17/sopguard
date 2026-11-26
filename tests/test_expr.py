import pytest

from sopguard.policy import expr


def env(**values):
    return expr.Env(values, {"double": lambda x: x * 2}, {"twice": "double(n)"})


def test_basic_eval():
    e = env(args={"amount": 50, "tags": ["a"]}, n=3)
    assert expr.evaluate("args.amount <= 100 and 'a' in args.tags", e) is True
    assert expr.evaluate("twice + 1", e) == 7
    assert expr.evaluate("args['amount'] * 2", e) == 100
    assert expr.evaluate("not false", e) is True


@pytest.mark.parametrize(
    "src",
    ["__import__('os')", "args.__class__", "(lambda: 1)()", "[x for x in args]", "args.get('x')", "a; b"],
)
def test_rejects_unsafe(src):
    with pytest.raises(expr.ExprError):
        expr.evaluate(src, env(args={}))


def test_missing_fact_is_explicit():
    e = env(order=None, args={})
    with pytest.raises(expr.MissingFact):
        expr.evaluate("order.total > 5", e)
    with pytest.raises(expr.MissingFact):
        expr.evaluate("args.amount", e)


def test_unknown_name():
    with pytest.raises(expr.ExprError):
        expr.evaluate("nope + 1", env())
