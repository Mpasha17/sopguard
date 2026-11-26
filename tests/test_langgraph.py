"""End-to-end LangGraph integration with a scripted chat model (no network)."""

from typing import Annotated, TypedDict

import pytest
from conftest import SOP, pick

pytest.importorskip("langgraph")
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.graph.message import add_messages  # noqa: E402

from sopguard.adapters.langgraph import ReplyGuardNode, SupervisedToolNode, route_after_guard  # noqa: E402
from sopguard.sdk import Guard  # noqa: E402


class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    sopguard_escalated: bool


def build(guard, script, tools):
    it = iter(script)

    def agent(state):
        return {"messages": [next(it)]}

    def after_agent(state):
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else "guard"

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", SupervisedToolNode(tools, guard))
    g.add_node("guard", ReplyGuardNode(guard))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", after_agent, {"tools": "tools", "guard": "guard"})
    g.add_edge("tools", "agent")
    g.add_conditional_edges("guard", route_after_guard, {"agent": "agent", "end": END})
    return g.compile()


def test_tool_call_is_steered_and_bad_reply_replaced(shop):
    lo = pick(shop)
    executed = []

    def issue_refund(order_id: str, amount: float):
        executed.append(order_id)
        return {"refund_id": "x"}

    script = [
        AIMessage(
            content="",
            tool_calls=[{"id": "c1", "name": "issue_refund", "args": {"order_id": lo["order_id"], "amount": 10}}],
        ),
        AIMessage(content="Your refund has been issued!", id="m2"),
        AIMessage(content="Please share the email on the order and your ZIP code so I can verify you.", id="m3"),
    ]
    guard = Guard(SOP)
    app = build(guard, script, {"issue_refund": issue_refund, "lookup_order": shop.lookup_order})
    out = app.invoke(
        {"messages": [HumanMessage(content=f"refund {lo['order_id']}")]}, {"configurable": {"thread_id": "lg1"}}
    )
    msgs = out["messages"]
    assert executed == []
    tool_msg = next(m for m in msgs if isinstance(m, ToolMessage))
    assert tool_msg.status == "error" and "verify-before-change" in tool_msg.content
    assert not any(getattr(m, "content", "") == "Your refund has been issued!" for m in msgs)
    assert msgs[-1].content.startswith("Please share the email")
    assert [r["action"] for r in guard.audit.for_session("lg1")] == ["steer", "steer", "allow"]
