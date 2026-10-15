import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()
from llm import config_banner
print(config_banner())

from db import checkpointer_single
from graph import build_graph
from repl import repl
from runtime_env import selector_loop_factory
from context import RunContext


async def main() -> None:
    # CLI = lokalne narzędzie operatora: surowy thread_id, bez scoped_thread_id
    # i bez sprawdzenia własności. Wątki z CLI nie trafiają do rejestru ani do UI.
    thread_id = sys.argv[1] if len(sys.argv) > 1 else "cli"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        async with checkpointer_single() as cp:
            graph = build_graph(checkpointer=cp)
            await repl(graph, config, RunContext())
    except (RuntimeError, ValueError) as e:
        print(f"Błąd konfiguracji lub grafu: {type(e).__name__}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Błąd połączenia z bazą: {type(e).__name__}. Sprawdź DATABASE_URL w .env.")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main(), loop_factory=selector_loop_factory())
    except KeyboardInterrupt:
        print("\nPrzerwano.")