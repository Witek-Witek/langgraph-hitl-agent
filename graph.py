from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.prebuilt import ToolNode

from nodes import classify, assistant, tool_gate, deny, respond_simple
from routing import route_after_classify, route_after_assistant, route_after_gate
from state import AgentState
from tools import TOOLS
from context import RunContext


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(AgentState, context_schema=RunContext)
    g.add_node("classify", classify)
    g.add_node("respond_simple", respond_simple)
    g.add_node("assistant", assistant)
    g.add_node("tool_gate", tool_gate)      
    g.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))
    g.add_node("deny_policy", partial(deny, reason="policy"))
    g.add_node("deny_cap", partial(deny, reason="cap"))

    g.add_edge(START, "classify")
    g.add_conditional_edges(
        "classify",
        route_after_classify,
        {"simple": "respond_simple", "assistant": "assistant"},
    )
    g.add_conditional_edges(
        "assistant", 
        route_after_assistant,
        {"end": END, "cap": "deny_cap", "gate": "tool_gate", "tools": "tools"}
    )
    g.add_conditional_edges(
        "tool_gate",
        route_after_gate,
        {"approved": "tools", "denied": "deny_policy"}, 
    )
    g.add_edge("respond_simple", END)
    # Zbędna krawędź: g.add_edge("assistant", END)
    g.add_edge("deny_policy", END)
    g.add_edge("deny_cap", END)
    g.add_edge("tools", "assistant")
    return g.compile(checkpointer=checkpointer)


def studio_graph():
    return build_graph()