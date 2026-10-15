import os
import asyncio
import json

from fastapi import APIRouter, HTTPException, Request, Body, Depends
from langgraph.types import Command
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage
from sse_starlette.sse import EventSourceResponse

from server.schemas import ChatRequest, ResumeRequest, FinishRequest
from context import RunContext
from llm import resolve_profile, public_profiles
from prompts import resolve_persona, public_personas
from auth import get_current_user
from server.scoping import scoped_thread_id


router = APIRouter()

RESPONSE_NODES = {"assistant", "respond_simple"}
SIDE_EFFECT_NODES = {"tools"}          # node'y mogące wykonać efekty uboczne
DENY_NODES = {"deny_policy", "deny_cap"}
HEARTBEAT_SEC = 15
TOOL_ARG_LIMIT = 200
TOOL_RESULT_LIMIT = 500
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_NOT_FOUND = HTTPException(404, "Thread not found")


def _ctx(profile: str = "", persona: str = "") -> RunContext:
    return RunContext(profile=profile, persona=persona)


def _pending_kind(snap) -> str | None:
    """None = wątek wolny; 'gate' = czeka na decyzję; 'execution' = przerwane wykonanie."""
    if not snap.next:
        return None
    return "gate" if getattr(snap, "interrupts", ()) else "execution"

_BUSY = "Wątek ma niedokończone wykonanie. Użyj POST /threads/<thread_id>/finish albo nowego thread_id."


def _gate_payload(snap) -> dict:
    interrupts = getattr(snap, "interrupts", ())
    return interrupts[0].value if interrupts else {}


def _trim(value, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}… (+{len(text) - limit} zn.)"


def _text(content) -> str:
    """AIMessage.content bywa listą bloków (Anthropic, część modeli OpenAI)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)


def _tool_events(node: str, delta: dict) -> list[dict]:
    """Zamienia deltę node'a na zdarzenia tool_start / tool_end (D-18: przycinanie)."""
    if not isinstance(delta, dict):        # __interrupt__ → krotka Interrupt, nie delta State
        return []
    
    events = []
    for msg in delta.get("messages", []) or []:
        if node == "assistant":
            for call in getattr(msg, "tool_calls", None) or []:
                events.append({
                    "event": "tool_start",
                    "data": json.dumps({
                        "id": call["id"],
                        "name": call["name"],
                        "args": {k: _trim(v, TOOL_ARG_LIMIT) for k, v in call.get("args", {}).items()},
                    }),
                })

        elif node in DENY_NODES:
            if isinstance(msg, ToolMessage):
                events.append({
                    "event": "tool_end",
                    "data": json.dumps({
                        "id": msg.tool_call_id,
                        "name": msg.name,
                        "result": _trim(msg.content, TOOL_RESULT_LIMIT),
                        "status": "error",
                    }),
                })
            elif isinstance(msg, AIMessage):
                events.append({"event": "token", "data": json.dumps({"content": _text(msg.content)})})

        elif node == "tools" and isinstance(msg, ToolMessage):
            events.append({
                "event": "tool_end",
                "data": json.dumps({
                    "id": msg.tool_call_id,   
                    "name": msg.name,
                    "result": _trim(msg.content, TOOL_RESULT_LIMIT),
                    "status": getattr(msg, "status", "success"),
                }),
            })
    return events


def _events_from(mode: str, payload) -> list[dict]:
    """Zamienia jeden fragment strumienia grafu na listę zdarzeń SSE."""
    if mode == "messages":
        chunk, metadata = payload
        if metadata.get("langgraph_node") not in RESPONSE_NODES:
            return []
        text = _text(getattr(chunk, "content", ""))
        if not text:
            return []                      # chunk niosący wyłącznie tool_use
        return [{"event": "token", "data": json.dumps({"content": text})}]

    if mode == "updates":
        print("UPDATES PAYLOAD:", repr(payload)[:400])    # ← tymczasowo
        return [
            event
            for node, delta in payload.items()
            for event in _tool_events(node, delta or {})
        ]

    return []   # nieznany tryb — ignoruj zamiast wywracać strumień


async def _thread_config(request: Request, user_id: str, thread_id: str,
                         ctx: RunContext | None = None) -> dict:
    """Tożsamość → własność → config. Dopiero po tym wolno czytać checkpoint."""
    if not await request.app.state.threads.owns(user_id, thread_id):
        raise _NOT_FOUND
    meta = {"user_id": user_id}
    if ctx is not None:
        meta |= {
            "profile": resolve_profile(ctx.profile),
            "persona": resolve_persona(ctx.persona),
            "profile_requested": ctx.profile or None,   # opcjonalnie: czy użytkownik wybrał
        }
    return {
        "configurable": {"thread_id": scoped_thread_id(user_id, thread_id)},
        "metadata": meta,
    }


async def stream_run(request: Request, graph, config, graph_input, ctx: RunContext):
    # config = {
    #     **config,
    #     "metadata": {
    #         "profile": resolve_profile(ctx.profile),
    #         "persona": resolve_persona(ctx.persona),
    #     },
    # }

    queue: asyncio.Queue = asyncio.Queue()

    async def heartbeat_worker():
        while True:
            await asyncio.sleep(HEARTBEAT_SEC)
            await queue.put({"event": "heartbeat", "data": "{}"})

    hb = asyncio.create_task(heartbeat_worker())
    agen = graph.astream(graph_input, config, context=ctx, stream_mode=["messages", "updates"])

    next_chunk = asyncio.create_task(anext(agen))
    next_beat = asyncio.create_task(queue.get())
    
    try:
        while True:
            done, _ = await asyncio.wait(
                {next_chunk, next_beat}, return_when=asyncio.FIRST_COMPLETED
            )

            if next_beat in done:
                if await request.is_disconnected():      # ← DODAJ
                    return
                yield next_beat.result()
                next_beat = asyncio.create_task(queue.get())

            if next_chunk in done:
                if await request.is_disconnected():
                    return
                try:
                    mode, payload = next_chunk.result()
                except StopAsyncIteration:
                    break
                for event in _events_from(mode, payload):
                    yield event
                next_chunk = asyncio.create_task(anext(agen))

    # Strumień wyczerpany — czy graf zawisł na bramce?
        snap = await graph.aget_state(config)
        if _pending_kind(snap) == "gate":
            yield {"event": "gate", "data": json.dumps(_gate_payload(snap))}
        else:
            yield {"event": "done", "data": json.dumps({"status": "complete"})}

        # terminacja bez zmian: _pending_kind → gate albo done
    except Exception as e:
        import traceback; traceback.print_exc()      # ← tymczasowo, do logu serwera
        yield {"event": "error", "data": json.dumps({"error": type(e).__name__})}
    finally:
        hb.cancel()
        for fut in (next_chunk, next_beat):
            fut.cancel()
        await asyncio.gather(hb, next_chunk, next_beat, return_exceptions=True)
        await agen.aclose()


@router.post("/chat/stream")
async def stream_chat(request: Request, body: ChatRequest, user_id: str = Depends(get_current_user)):
    graph = request.app.state.graph
    ctx = _ctx(body.profile, body.persona)
    config = await _thread_config(request, user_id, body.thread_id, ctx)

    snap = await graph.aget_state(config)
    kind = _pending_kind(snap)
    if kind == "execution":
        raise HTTPException(status_code=409, detail=_BUSY)
    if kind == "gate":
        raise HTTPException(status_code=409, detail="Wątek oczekuje na decyzję — użyj /resume.")

    await request.app.state.threads.touch(user_id, body.thread_id)
    graph_input = {"messages": [HumanMessage(content=body.message)]}
    return EventSourceResponse(
        stream_run(request, graph, config, graph_input, ctx),
        headers=SSE_HEADERS,
    )


@router.post("/threads/{thread_id}/resume")
async def resume_thread(request: Request, thread_id: str, body: ResumeRequest, user_id: str = Depends(get_current_user)):
    graph = request.app.state.graph
    ctx = _ctx(body.profile, body.persona)
    config = await _thread_config(request, user_id, thread_id, ctx)

    snap = await graph.aget_state(config)
    if _pending_kind(snap) != "gate":
        raise HTTPException(status_code=409, detail="Wątek nie oczekuje na decyzję.")

    await request.app.state.threads.touch(user_id, thread_id)
    decision = {"action": body.action, "note": body.note}
    return EventSourceResponse(
        stream_run(request, graph, config, Command(resume=decision), ctx),
        headers=SSE_HEADERS
    )


@router.post("/threads/{thread_id}/finish")
async def finish_thread(
    request: Request, 
    thread_id: str, 
    body: FinishRequest = Body(default_factory=FinishRequest), 
    user_id: str = Depends(get_current_user)
    ):
    graph = request.app.state.graph
    ctx = _ctx(body.profile, body.persona)
    config = await _thread_config(request, user_id, thread_id, ctx)
    
    snap = await graph.aget_state(config)
    if _pending_kind(snap) != "execution":
        raise HTTPException(status_code=409, detail="Brak przerwanego wykonania do dokończenia.")
    await request.app.state.threads.touch(user_id, thread_id)
    return EventSourceResponse(
        stream_run(request, graph, config, None, ctx),
        headers=SSE_HEADERS,
    )


@router.get("/threads/{thread_id}/state")
async def get_thread_state(request: Request, thread_id: str, user_id: str = Depends(get_current_user)):
    graph = request.app.state.graph
    config = await _thread_config(request, user_id, thread_id)
    snap = await graph.aget_state(config)
    kind = _pending_kind(snap)
    pending = list(snap.next)
    return {
        "thread_id": thread_id,
        "pending_kind": kind,                                    # None | "gate" | "execution"
        "pending_node": pending[0] if pending else None,         # NOWE
        "side_effects_possible": bool(set(pending) & SIDE_EFFECT_NODES),   # NOWE
        "next": pending,
        "gate": _gate_payload(snap) if kind == "gate" else None,
    }


@router.get("/threads/{thread_id}/history")
async def get_history(request: Request, thread_id: str, user_id: str = Depends(get_current_user)):
    graph = request.app.state.graph
    config = await _thread_config(request, user_id, thread_id)
    snap = await graph.aget_state(config)
    messages = snap.values.get("messages", [])

    # Argumenty żyją w AIMessage.tool_calls, wynik w ToolMessage — parujemy po id.
    call_args = {
        call["id"]: call.get("args", {})
        for m in messages
        for call in (getattr(m, "tool_calls", None) or [])
    }

    out = []
    for m in messages:
        item = {"role": m.type, "content": _text(m.content)}
        if isinstance(m, ToolMessage):
            item |= {
                "id": m.tool_call_id,
                "name": m.name,
                "status": getattr(m, "status", None),
                "args": call_args.get(m.tool_call_id, {}),
            }
        out.append(item)

    return {"thread_id": thread_id, "messages": out}


CONTRACT_VERSION = "10.0"

@router.get("/config/options")
async def config_options():
    """Katalog wyborów dostępnych dla klienta + wersja kontraktu."""
    return {
        "contract_version": CONTRACT_VERSION,
        "profiles": public_profiles(),
        "personas": public_personas(),
        "defaults": {
            "profile": os.environ.get("LLM_DEFAULT_PROFILE", ""),
            "persona": os.environ.get("LLM_DEFAULT_PERSONA", ""),
        },
    }