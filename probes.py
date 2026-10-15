# Wywołanie:
# uv run python -c "from probes import probe_tool_support; print(probe_tool_support('main'))"
# uv run python -c "from probes import probe_all_roles; probe_all_roles()" 
# lub uv run python probes.py

def probe_tool_support(role: str = "main") -> tuple[bool, str, float]:
    """Zwraca (wspiera, komunikat, latencja_s). Wywoływać ręcznie, nie przy starcie."""
    import time
    from langchain_core.tools import tool
    from llm import get_llm

    @tool
    def _ping(x: str) -> str:
        """Zwraca podany tekst. Użyj tego narzędzia zawsze."""
        return x

    t0 = time.perf_counter()
    try:
        msg = get_llm(role).bind_tools([_ping]).invoke("Użyj narzędzia _ping z x='ok'.")
        dt = time.perf_counter() - t0
        ok = bool(getattr(msg, "tool_calls", None))
        return ok, "tool_calls: TAK" if ok else "tool_calls: NIE (model odpowiedział tekstem)", dt
    except Exception as e:
        dt = time.perf_counter() - t0
        return False, f"BŁĄD: {type(e).__name__}", dt


def probe_all_roles() -> int:
    """Zwraca kod wyjścia: 0 = wszystkie role OK, 1 = przynajmniej jedna bez wsparcia."""
    from llm import describe_config

    failed = 0
    for role, spec in describe_config().items():
        ok, msg, dt = probe_tool_support(role)
        mark = "OK " if ok else "!! "
        print(f"{mark}{role:<12} {spec:<45} {msg}  ({dt:.2f}s)")
        failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    sys.exit(probe_all_roles())