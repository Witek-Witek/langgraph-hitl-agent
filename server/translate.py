"""Translate LangGraph stream chunks into contract events.

Pure functions: no HTTP, no I/O. Used by the background run (server/runs.py)
and, until it is replaced, by the legacy SSE endpoint.
"""
from langchain_core.messages import AIMessage, ToolMessage

from server.events import Done, Gate, GateCall, Token, ToolEnd, ToolStart, _Event

RESPONSE_NODES = {"assistant", "respond_simple"}
DENY_NODES = {"deny_policy", "deny_cap"}
TOOL_ARG_LIMIT = 200
TOOL_RESULT_LIMIT = 500


def text_of(content) -> str:
    """AIMessage.content may be a list of blocks (Anthropic, some OpenAI models)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def trim(value, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}… (+{len(text) - limit} chars)"


def pending_kind(snap) -> str | None:
    """None = thread idle; 'gate' = waiting for a decision; 'execution' = interrupted run."""
    if not snap.next:
        return None
    return "gate" if getattr(snap, "interrupts", ()) else "execution"


def gate_payload(snap) -> dict:
    interrupts = getattr(snap, "interrupts", ())
    return interrupts[0].value if interrupts else {}


def terminal_event(snap) -> _Event:
    """Final event of a completed stream: gate if the graph paused on approval, else done."""
    if pending_kind(snap) == "gate":
        raw = gate_payload(snap)
        return Gate(
            question=raw.get("question", ""),
            calls=[GateCall(**c) for c in raw.get("calls", [])],
        )
    return Done()


def _tool_end(msg: ToolMessage, status: str) -> ToolEnd:
    return ToolEnd(
        id=msg.tool_call_id,
        name=msg.name or "",
        result=trim(text_of(msg.content), TOOL_RESULT_LIMIT),
        status="error" if status == "error" else "ok",      # ToolMessage uses "success"
    )


def _from_update(node: str, delta) -> list[_Event]:
    if not isinstance(delta, dict):          # __interrupt__ carries a tuple, not a state delta
        return []
    events: list[_Event] = []
    for msg in delta.get("messages") or []:
        if node == "assistant":
            for call in getattr(msg, "tool_calls", None) or []:
                events.append(ToolStart(
                    id=call["id"],
                    name=call["name"],
                    args={k: trim(v, TOOL_ARG_LIMIT) for k, v in call.get("args", {}).items()},
                ))
        elif node in DENY_NODES:
            if isinstance(msg, ToolMessage):
                events.append(_tool_end(msg, "error"))
            elif isinstance(msg, AIMessage) and (text := text_of(msg.content)):
                events.append(Token(content=text))
        elif node == "tools" and isinstance(msg, ToolMessage):
            events.append(_tool_end(msg, getattr(msg, "status", "success")))
    return events


def translate(mode: str, payload) -> list[_Event]:
    """One graph stream chunk → zero or more contract events."""
    if mode == "messages":
        chunk, metadata = payload
        if metadata.get("langgraph_node") not in RESPONSE_NODES:
            return []
        text = text_of(getattr(chunk, "content", ""))
        return [Token(content=text)] if text else []
    if mode == "updates":
        return [ev for node, delta in payload.items() for ev in _from_update(node, delta or {})]
    return []                                 # unknown mode: ignore instead of breaking the stream