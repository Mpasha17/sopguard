"""Policy compiler: SOP written in Markdown (with fenced ``sopguard`` YAML blocks) or plain YAML -> :class:`Policy`.

The Markdown stays the human source of truth. Each fenced block is compiled into typed rules and every rule
gets a citation pointing back to the SOP section it came from, so every supervisor decision can say *which
line of the SOP* it enforced.

    ## 2. Identity verification
    Agents must verify the customer before changing anything on the account.

    ```sopguard
    - id: verify-before-refund
      type: precondition
      tools: [issue_refund]
      require: verified(order.customer_id)
    ```
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from sopguard.policy import expr
from sopguard.policy.schema import Policy

FENCE = re.compile(r"^```(?:yaml\s+)?sopguard[^\n]*\n(.*?)^```", re.S | re.M)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)

# Names every expression may use. Helpers are implemented in sopguard.runtime.state.
BASE_NAMES = {"args", "tool", "message", "owner", "session", "true", "false", "null", "True", "False", "None"}
HELPERS = {
    "called",
    "count",
    "last_result",
    "verified",
    "user_said",
    "sum_args",
    "approved",
    "lower",
    "len",
    "abs",
    "min",
    "max",
    "contains",
    "days_between",
    "contains_any",
    "matches",
    "has_pii",
}


class PolicyError(ValueError):
    pass


def _section_for(pos: int, headings: list[tuple[int, int, str]]) -> str:
    current = ""
    for start, _level, text in headings:
        if start > pos:
            break
        current = text
    return current


def _strip_md(text: str) -> str:
    return re.sub(r"[*_`]", "", text).strip()


def compile_markdown(md: str) -> Policy:
    headings = [(m.start(), len(m.group(1)), _strip_md(m.group(2))) for m in HEADING.finditer(md)]
    meta: dict[str, Any] = {}
    rules: list[dict[str, Any]] = []
    blocks = list(FENCE.finditer(md))
    if not blocks:
        raise PolicyError("no ```sopguard blocks found in the SOP")
    for m in blocks:
        try:
            data = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            line = md[: m.start()].count("\n") + 2
            raise PolicyError(f"YAML error in sopguard block starting at line {line}: {e}") from e
        section = _section_for(m.start(), headings)
        if isinstance(data, dict) and "policy" in data:
            meta.update(data["policy"] or {})
            data = data.get("rules", [])
        if isinstance(data, dict) and "rules" in data:
            data = data["rules"]
        if isinstance(data, dict):
            data = [data]
        for r in data or []:
            if not isinstance(r, dict):
                raise PolicyError(f"rule entries must be mappings, got {r!r}")
            r.setdefault("citation", f"SOP § {section}" if section else "SOP")
            rules.append(r)
    if "name" not in meta:
        title = next((t for _s, lvl, t in headings if lvl == 1), "policy")
        meta["name"] = title
    return _build({**meta, "rules": rules}, md)


def compile_yaml(text: str) -> Policy:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise PolicyError("policy YAML must be a mapping")
    if "policy" in data:
        data = {**data.pop("policy"), **data}
    return _build(data, text)


def _build(data: dict[str, Any], source: str) -> Policy:
    data["source_hash"] = hashlib.sha256(source.encode()).hexdigest()[:16]
    try:
        policy = Policy.model_validate(data)
    except ValidationError as e:
        raise PolicyError(_pretty(e)) from e
    except expr.ExprError as e:
        raise PolicyError(str(e)) from e
    _lint(policy)
    return policy


def _pretty(e: ValidationError) -> str:
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        out.append(f"{loc}: {err['msg']}")
    return "invalid policy:\n  " + "\n  ".join(out)


def _lint(policy: Policy) -> None:
    allowed = BASE_NAMES | HELPERS | set(policy.lets)
    for name, e in policy.lets.items():
        unknown = expr.names(e) - allowed
        if unknown:
            raise PolicyError(f"let {name!r} uses unknown names {sorted(unknown)}")
    for r in policy.rules:
        for e in r.expressions():
            unknown = expr.names(e) - allowed
            if unknown:
                raise PolicyError(f"rule {r.id!r}: unknown names {sorted(unknown)} in {e!r}")


def compile_policy(text: str, fmt: str = "auto") -> Policy:
    if fmt == "auto":
        fmt = "markdown" if "```" in text else "yaml"
    return compile_markdown(text) if fmt == "markdown" else compile_yaml(text)


def load_policy(path: str | Path) -> Policy:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return compile_policy(text, "markdown" if p.suffix.lower() in (".md", ".markdown") else "auto")


def explain(policy: Policy) -> str:
    """Human-readable summary of what the compiler produced (``sopguard compile``)."""
    lines = [f"Policy: {policy.name} (v{policy.version}, hash {policy.source_hash})", f"{len(policy.rules)} rules:"]
    for r in policy.rules:
        scope = ",".join(r.tools) if r.target == "tool_call" and r.tools else r.target
        lines.append(f"  [{r.type:<15}] {r.id:<34} on {scope:<28} -> {r.on_violation.value:<8} {r.citation}")
    return "\n".join(lines)
