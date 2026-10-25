"""Event contract v2.0 for run streams.

Every event sent to the client is defined here. The client mirrors these
models with zod (ui/src/schemas/events.js); a contract test validates both
against the same recorded run.
"""
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

CONTRACT_VERSION = "2.0"

TERMINAL_TYPES = frozenset({"done", "gate", "error", "cancelled", "interrupted"})
UNLOGGED_TYPES = frozenset({"hello", "heartbeat"})   # never persisted, never carry an SSE id


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    unpersisted: bool = False    # set when the log write failed: the event was sent live only


# --- Connection-level events (outside the numbered log) -----------------------

class Hello(_Event):
    type: Literal["hello"] = "hello"
    contract_version: str = CONTRACT_VERSION
    run_id: str
    status: str
    last_seq: int


class Heartbeat(_Event):
    type: Literal["heartbeat"] = "heartbeat"


# --- Logged events (numbered, replayable) -------------------------------------

class Token(_Event):
    type: Literal["token"] = "token"
    content: str


class ToolStart(_Event):
    type: Literal["tool_start"] = "tool_start"
    id: str
    name: str
    args: dict = {}


class ToolEnd(_Event):
    type: Literal["tool_end"] = "tool_end"
    id: str
    name: str
    result: str
    status: Literal["ok", "error"]


class GateCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    args: dict
    sensitive: bool


class Gate(_Event):
    type: Literal["gate"] = "gate"
    gate_type: Literal["tool_approval"] = "tool_approval"
    question: str
    calls: list[GateCall]


class Done(_Event):
    type: Literal["done"] = "done"


class Error(_Event):
    type: Literal["error"] = "error"
    error: str                        # exception class name only, never the message


class Cancelled(_Event):
    type: Literal["cancelled"] = "cancelled"


class Interrupted(_Event):
    type: Literal["interrupted"] = "interrupted"


Event = Annotated[
    Hello | Heartbeat | Token | ToolStart | ToolEnd | Gate | Done | Error | Cancelled | Interrupted,
    Field(discriminator="type"),
]
EventAdapter = TypeAdapter(Event)


def to_payload(event: _Event) -> dict:
    """Body stored in run_events.payload and sent as SSE data (type lives in the envelope)."""
    data = event.model_dump(exclude={"type"})
    if not data.get("unpersisted"):
        data.pop("unpersisted", None)
    return data


def to_sse(event: _Event, seq: int | None = None) -> dict:
    """Envelope for sse-starlette. Logged events carry `id`; hello/heartbeat never do."""
    out = {"event": event.type, "data": json.dumps(to_payload(event), ensure_ascii=False)}
    if seq is not None and event.type not in UNLOGGED_TYPES:
        out["id"] = str(seq)
    return out