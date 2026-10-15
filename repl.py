import asyncio

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from context import RunContext


def _extract_gate_payload(snap) -> dict:
    interrupts = getattr(snap, "interrupts", ())
    if interrupts:
        return interrupts[0].value          # StateSnapshot.interrupts[].value (zweryfikowane w API ref)
    return {}


def _format_calls(payload: dict) -> str:
    lines = []
    for c in payload.get("calls", []):
        mark = "⚠" if c.get("sensitive") else " "
        lines.append(f"  {mark} {c['name']}({_short_args(c.get('args', {}))})")
    return "\n".join(lines)


def _short_args(args: dict, limit: int = 120) -> str:
    parts = []
    for k, v in args.items():
        text = str(v)
        if len(text) > limit:
            text = text[:limit] + f"… (+{len(str(v)) - limit} zn.)"
        parts.append(f"{k}={text!r}")
    return ", ".join(parts)


async def _drain_gate(graph, config, ctx: RunContext) -> None:
    """Jeśli graf zawisł na bramce, pytaj o decyzję i wznawiaj aż do końca tury."""
    while True:
        snap = await graph.aget_state(config)
        if not snap.next:                       # brak zawieszenia → tura zakończona
            return
        if not getattr(snap, "interrupts", ()):
            print(f"\n[przerwane wykonanie] czeka: {list(snap.next)} — uruchom ponownie wątek, by dokończyć.")
            return

        payload = _extract_gate_payload(snap)
        print(f"\n[BRAMKA] {payload.get('question', 'Wymagana decyzja')}")
        print(_format_calls(payload))                      # ZMIANA: paczka zamiast preview
        # if payload.get("preview"):
        #     print(f"  preview: {payload['preview']}")

        choice = input("  [a]kceptuj / [o]drzuć: ").strip().lower()
        decision = {"action": "approve"} if choice == "a" else {"action": "reject"}

        # if choice == "a":
        #     decision = {"action": "approve"}
        # else:
        #     note = input("  notatka (Enter = brak): ").strip() or None
        #     decision = {"action": "reject", "note": note}
        try:
            result = await graph.ainvoke(Command(resume=decision), config, context=ctx)
        except Exception as e:
            print(f"Błąd wznowienia: {type(e).__name__}: {e}")
            return

        print(f"[gate={result.get('gate_decision')}] "
              f"[iter={result.get('tool_iterations')}]")      
        # print(f"[route={result.get('route')}] [gate={result.get('gate_decision')}]")
        print(result["messages"][-1].content)


async def repl(graph, config, ctx: RunContext) -> None:
    thread_id = config["configurable"]["thread_id"]
    print(f"Wątek: {thread_id}. Pisz; exit/quit/pusta linia kończy.")

    while True:
        try:
            text = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if text in ("", "exit", "quit"):
            break

        try:
            result = await graph.ainvoke({"messages": [HumanMessage(content=text)]}, config, context=ctx)
        except Exception as e:
            print(f"Błąd tury: {type(e).__name__}: {e}")
            continue

        print(f"[route={result['route']}]")
        print(result["messages"][-1].content)

        await _drain_gate(graph, config, ctx)
    try:
        snap = await graph.aget_state(config)
        msgs = snap.values.get("messages", [])
        turns = sum(1 for m in msgs if m.type == "human")
        print(f"\nWątek {thread_id}: {len(msgs)} wiadomości, {turns} tur.")
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\nPrzerwano.")