from state import AgentState
from tools import has_sensitive 

_LABELS = ("simple", "complex")
TOOL_ITERATIONS_MAX = 5


def route_after_classify(state: AgentState) -> str:   # ZMIANA: „complex" → bramka
    return "simple" if state.get("route") == "simple" else "assistant"


def route_after_assistant(state: AgentState) -> str:
    last = state["messages"][-1]
    calls = getattr(last, "tool_calls", None)
    if not calls:
        return "end"                                        # model odpowiedział — koniec tury
    if state.get("tool_iterations", 0) >= TOOL_ITERATIONS_MAX:
        return "cap"                                        # bezpiecznik pętli
    return "gate" if has_sensitive(calls) else "tools"
    

def route_after_gate(state: AgentState) -> str:        # NOWY
    return "approved" if state.get("gate_decision") == "approve" else "denied"