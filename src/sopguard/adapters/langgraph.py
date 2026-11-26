"""LangGraph adapter.

* :class:`SupervisedToolNode` - drop-in replacement for ``ToolNode``: every tool call in the last ``AIMessage``
  is checked first; allowed calls run, stopped calls return a ``ToolMessage`` carrying the supervisor's
  feedback (status="error"), so the model sees why and retries - that is the steer.
* :class:`ReplyGuardNode` - put it after your agent node: checks the final reply; if it is not allowed it
  removes it and adds the feedback so you can route back to the agent (see :func:`route_after_guard`).

The conversation id comes from ``config["configurable"]["thread_id"]`` (LangGraph's own convention).
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from sopguard.runtime.decision import Action
from sopguard.sdk import HANDOFF_TEXT, Guard

ESCALATED_KEY = "sopguard_escalated"


def _session_id(config: RunnableConfig | dict[str, Any] | None) -> str:
    return str(((config or {}).get("configurable") or {}).get("thread_id", "default"))  # type: ignore[union-attr]


def _sync_user_messages(guard: Guard, sid: str, messages: Sequence[Any]) -> None:
    s = guard.session(sid)
    seen = len(s.session.state.user_messages)
    humans = [m for m in messages if isinstance(m, HumanMessage) and not getattr(m, "name", None) == "supervisor"]
    for m in humans[seen:]:
        s.user(m.content if isinstance(m.content, str) else json.dumps(m.content))


class SupervisedToolNode:
    def __init__(self, tools: Sequence[Any] | dict[str, Callable[..., Any]], guard: Guard) -> None:
        if isinstance(tools, dict):
            self.tools = dict(tools)
        else:
            self.tools = {getattr(t, "name", getattr(t, "__name__", str(t))): t for t in tools}
        self.guard = guard

    def _run(self, tool: Any, args: dict[str, Any]) -> Any:
        if hasattr(tool, "invoke"):
            return tool.invoke(args)
        return tool(**args)

    def __call__(self, state: dict[str, Any], config: RunnableConfig | None = None) -> dict[str, Any]:
        sid = _session_id(config)
        messages = state["messages"]
        _sync_user_messages(self.guard, sid, messages)
        last = messages[-1]
        out: list[Any] = []
        escalated = False
        s = self.guard.session(sid)
        for call in getattr(last, "tool_calls", []) or []:
            name, args = call["name"], dict(call.get("args") or {})
            if name not in self.tools:
                out.append(ToolMessage(content=f"unknown tool {name}", tool_call_id=call["id"], status="error"))
                continue
            d = s.check_tool(name, args)
            if not d.allowed:
                escalated |= d.action == Action.ESCALATE
                out.append(ToolMessage(content=d.agent_feedback(), tool_call_id=call["id"], status="error", name=name))
                continue
            result = self._run(self.tools[name], args)
            s.record(name, args, result)
            content = result if isinstance(result, str) else json.dumps(result, default=str)
            out.append(ToolMessage(content=content, tool_call_id=call["id"], name=name))
        update: dict[str, Any] = {"messages": out}
        if escalated:
            update[ESCALATED_KEY] = True
            update["messages"] = out + [AIMessage(content=HANDOFF_TEXT)]
        return update


class ReplyGuardNode:
    def __init__(self, guard: Guard) -> None:
        self.guard = guard

    def __call__(self, state: dict[str, Any], config: RunnableConfig | None = None) -> dict[str, Any]:
        sid = _session_id(config)
        messages = state["messages"]
        _sync_user_messages(self.guard, sid, messages)
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls or not isinstance(last.content, str):
            return {}
        r = self.guard.session(sid).reply(last.content)
        if r.allowed:
            return {}
        remove = [RemoveMessage(id=last.id)] if last.id else []
        if r.escalated:
            return {"messages": remove + [AIMessage(content=HANDOFF_TEXT)], ESCALATED_KEY: True}
        return {"messages": remove + [HumanMessage(content=r.feedback, name="supervisor")]}


def route_after_guard(state: dict[str, Any]) -> str:
    """Conditional edge after :class:`ReplyGuardNode`: ``"agent"`` to retry after a steer, else ``"end"``."""
    last = state["messages"][-1]
    if isinstance(last, HumanMessage) and getattr(last, "name", None) == "supervisor":
        return "agent"
    return "end"
