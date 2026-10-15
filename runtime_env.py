import asyncio
import selectors
import sys

# def apply_windows_selector_event_loop() -> None:
#     if sys.platform == "win32":
#         asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def selector_loop_factory():
    """Windows: psycopg nie działa na ProactorEventLoop (domyślnym od 3.8).
    Zwraca fabrykę pętli selektorowej na Windows, None na innych platformach.
    Koszt: pętla selektorowa nie obsługuje podprocesów.
    """
    if sys.platform == "win32":
        return lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    return None