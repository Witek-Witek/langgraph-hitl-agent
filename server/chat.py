import os
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, Body, Depends
from langgraph.types import Command
from langchain_core.messages import HumanMessage, ToolMessage
from sse_starlette.sse import EventSourceResponse

from server.schemas import ChatRequest, ResumeRequest, FinishRequest
from context import RunContext
from llm import resolve_profile, public_profiles
from prompts import resolve_persona, public_personas
from auth import get_current_user
from server.scoping import scoped_thread_id
from server.events import CONTRACT_VERSION, Error, Heartbeat, to_sse
from server.translate import pending_kind, gate_payload, terminal_event, text_of, translate


log = logging.getLogger(__name__)
router = APIRouter()

SIDE_EFFECT_NODES = {"tools"}          # node'y mogące wykonać efekty uboczne
HEARTBEAT_SEC = 15
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_NOT_FOUND = HTTPException(404, "Thread not found")
_BUSY = "Thread has an unfinished run. Use POST /threads/<thread_id>/finish or a new thread_id."


def _ctx(profile: str = "", persona: str = "") -> RunContext:
    return RunContext(profile=profile, persona=persona)


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
            await queue.put(to_sse(Heartbeat())) 

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
                if await request.is_disconnected():
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
                for event in translate(mode, payload):
                    yield to_sse(event)
                next_chunk = asyncio.create_task(anext(agen))

        snap = await graph.aget_state(config)
        yield to_sse(terminal_event(snap))
    
    except Exception as e:
        import traceback; traceback.print_exc()      # ← tymczasowo, redundacja z log.exception, do logu serwera
        log.exception("stream failed")
        yield to_sse(Error(error=type(e).__name__))

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
    kind = pending_kind(snap)
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
    if pending_kind(snap) != "gate":
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
    if pending_kind(snap) != "execution":
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
    kind = pending_kind(snap)
    pending = list(snap.next)
    return {
        "thread_id": thread_id,
        "pending_kind": kind,                                    # None | "gate" | "execution"
        "pending_node": pending[0] if pending else None, 
        "side_effects_possible": bool(set(pending) & SIDE_EFFECT_NODES),
        "next": pending,
        "gate": to_payload(terminal_event(snap)) if kind == "gate" else None,
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
        item = {"role": m.type, "content": text_of(m.content)}
        if isinstance(m, ToolMessage):
            item |= {
                "id": m.tool_call_id,
                "name": m.name,
                "status": getattr(m, "status", None),
                "args": call_args.get(m.tool_call_id, {}),
            }
        out.append(item)

    return {"thread_id": thread_id, "messages": out}


@router.get("/config/options")
async def config_options(user_id: str = Depends(get_current_user)):
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