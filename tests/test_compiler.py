import pytest

from sopguard.policy.compiler import PolicyError, compile_policy, explain


def test_demo_sop_compiles(policy):
    ids = [r.id for r in policy.rules]
    assert len(ids) == 14 and len(set(ids)) == 14
    types = {r.type for r in policy.rules}
    assert {
        "sequence",
        "precondition",
        "arg_constraint",
        "threshold",
        "limit",
        "forbidden_claim",
        "pii",
        "semantic",
    } <= types
    r = next(r for r in policy.rules if r.id == "verify-before-change")
    assert r.citation == "SOP § 2. Identity verification"
    assert "manager-approval-over-200" in explain(policy)


MD = """# Tiny SOP
## Refunds
```sopguard
policy: {name: tiny, tools: {refund: x, verify: y}}
rules:
  - id: cap
    type: threshold
    tools: [refund]
    value: args.amount
    max: 100
```
"""


def test_markdown_and_yaml_formats():
    p = compile_policy(MD)
    assert p.name == "tiny" and p.rules[0].citation == "SOP § Refunds"
    y = compile_policy("name: y\nrules:\n  - {id: a, type: limit, tools: [t], max_count: 2}\n", "yaml")
    assert y.rules[0].type == "limit"


@pytest.mark.parametrize(
    "bad,msg",
    [
        (MD.replace("tools: [refund]", "tools: [refnud]"), "undeclared tools"),
        (MD.replace("args.amount", "args.amount +"), "invalid expression"),
        (MD.replace("args.amount", "secret.amount"), "unknown names"),
        (MD.replace("type: threshold", "type: magic"), "rules.0"),
        (MD.replace("max: 100", "max: 100\n  - {id: cap, type: limit, tools: [refund]}"), "duplicate rule id"),
        ("# nothing here", "no ```sopguard blocks"),
        (MD.replace("max: 100", "max: [unclosed"), "YAML error"),
    ],
)
def test_compiler_errors(bad, msg):
    with pytest.raises(PolicyError, match=msg):
        compile_policy(bad, "markdown")
