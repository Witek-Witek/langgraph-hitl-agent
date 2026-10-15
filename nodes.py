import json

from langgraph.types import interrupt
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage
from langgraph.runtime import Runtime

from state import AgentState, RouteLabel
from llm import get_profile_llm, get_role_llm
from tools import TOOLS, SENSITIVE
from context import RunContext
from prompts import compose_system, today


_LABELS = ("simple", "complex")
_DEFAULT_LABEL = "complex"


def _coerce_decision(decision) -> dict:
    """Resume value bywa dictem (API), JSON-stringiem (Studio) lub gołą akcją."""
    if isinstance(decision, dict):
        return decision
    if isinstance(decision, str):
        try:
            parsed = json.loads(decision)
        except (json.JSONDecodeError, ValueError):
            return {"action": decision.strip()}        # akceptuj też samo "approve"
        return parsed if isinstance(parsed, dict) else {"action": str(parsed)}
    return {}                                           # None/inne → reject (fail-safe)


def _normalize(text: str) -> RouteLabel:
    label = text.strip().lower().strip(".,:;!?\"'`*")
    return label if label in _LABELS else _DEFAULT_LABEL


def _describe_calls(tool_calls) -> list[dict]:
    """Czytelny opis paczki dla człowieka — bez efektów ubocznych."""
    return [
        {
            "id": c["id"], 
            "name": c["name"],
            "args": c.get("args", {}),
            "sensitive": c["name"] in SENSITIVE,
        }
        for c in tool_calls
    ]


CLASSIFY_SYSTEM = (
    "Sklasyfikuj pytanie użytkownika. Odpowiedz DOKŁADNIE jednym słowem:\n"
    "simple  — proste pytanie o fakt, da się odpowiedzieć w 1-2 zdaniach "
    "i nie wymaga danych spoza wiedzy modelu\n"
    "complex — wymaga wyjaśnienia, porównania, wielu kroków lub struktury  "
    "lub odpowiedź wymaga danych spoza wiedzy modelu (czas, zapis, pliki)\n"
    "Żadnego innego tekstu."
)

async def classify(state: AgentState) -> dict:
    """Bez kontekstu — klasyfikator jest decyzją operatora, nie użytkownika."""
    last = state["messages"][-1]
    raw = await get_role_llm("classifier").ainvoke(
        [SystemMessage(content=CLASSIFY_SYSTEM), last]
        )
    return {"route": _normalize(str(raw.content)), "tool_iterations": 0}   # ZMIANA: reset licznika


# def requires_approval(state: AgentState) -> bool:
#     """Czysta funkcja polityki — bez LLM, bez I/O."""
#     return state.get("route") == "complex"


# ASSISTANT_SYSTEM = (
#     "Jesteś asystentem z dostępem do narzędzi. Używaj ich, gdy są potrzebne "
#     "do odpowiedzi. Gdy masz komplet informacji — odpowiedz zwięźle."
# )

async def assistant(state: AgentState, runtime: Runtime[RunContext]) -> dict:
    ctx = runtime.context
    system = compose_system(ctx.persona, {"data": today()})
    model = get_profile_llm(ctx.profile).bind_tools(TOOLS) # Opcja: bind_tools(TOOLS, strict=True)
    response = await model.ainvoke(
        [SystemMessage(content=system), *state["messages"]]
        )
    return {
        "messages": [response],
        "tool_iterations": state.get("tool_iterations", 0) + 1,
    }


def tool_gate(state: AgentState) -> dict:
    # ⚠ ZERO efektów ubocznych przed interrupt() — po wznowieniu node startuje OD NOWA
    calls = state["messages"][-1].tool_calls
    payload = {
        "question": "Zatwierdzić wywołanie narzędzi z efektami ubocznymi?",
        "calls": _describe_calls(calls),
    }
    decision = interrupt(payload)
    data = _coerce_decision(decision)          # L-7: normalizacja typu
    if data.get("action") == "reject":
        return {"gate_decision": "reject"}
    return {"gate_decision": "approve"} if data.get("action") == "approve" else {"gate_decision": "reject"}


def deny(state: AgentState, reason: str = "policy") -> dict:
    """Domyka tool_calls sztucznymi ToolMessage — historia musi zostać spójna."""
    last = state["messages"][-1]
    note = {
        "policy": "Odmowa użytkownika — narzędzie nie zostało wywołane.",
        "cap": "Przekroczono limit iteracji narzędziowych.",
    }[reason]
    denials = [
        ToolMessage(content=note, tool_call_id=c["id"], name=c["name"], status="error")
        for c in last.tool_calls
    ]
    return {"messages": denials + [AIMessage(content=note)]}


# SIMPLE_SYSTEM = "Odpowiadaj maksymalnie zwięźle: 1-2 zdania, bez wstępów i dygresji."

async def respond_simple(state: AgentState, runtime: Runtime[RunContext]) -> dict:
    ctx = runtime.context
    system = compose_system(ctx.persona, {"data": today()})
    response = await get_profile_llm(ctx.profile).ainvoke(
        [SystemMessage(content=system), *state["messages"]]
        )
    return {"messages": [response]}