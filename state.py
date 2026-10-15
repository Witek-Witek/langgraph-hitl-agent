from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

RouteLabel = Literal["simple","complex"]
GateDecision = Literal["approve", "reject"]

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    route: RouteLabel | None             # Flaga sterująca dla routingu
    gate_decision: GateDecision | None   # Flaga sterująca dla routingu
    tool_iterations: int                 # Licznik przebiegów pętli ReAct

