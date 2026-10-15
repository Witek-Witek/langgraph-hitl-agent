import hashlib
import os
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

from langchain_core.tools import tool

SENSITIVE = {"save_note"}          # polityka: które narzędzia wymagają zgody


@cache
def workspace_dir() -> Path:
    """Granica uprawnień narzędzi + walidacja fail-fast (E-10)."""
    raw = os.environ.get("WORKSPACE_DIR")
    if not raw:
        raise RuntimeError("Brak WORKSPACE_DIR — ustaw katalog roboczy narzędzi w .env.")
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    try:
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")      # realny test praw zapisu
        probe.unlink()
        return path
    except PermissionError as e:
        raise RuntimeError(
            f"Brak uprawnień do zapisu w katalogu: {path}. "
            f"Błąd systemowy: {e} Zmień uprawnienia folderu."
        )
    

@tool
def get_time() -> str:
    """Zwraca aktualny czas UTC w formacie ISO 8601."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@tool
def save_note(text: str) -> str:
    """Zapisuje notatkę tekstową do pliku w katalogu roboczym. Zwraca nazwę pliku."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    target = workspace_dir() / f"note_{digest}.txt"
    target.write_text(text, encoding="utf-8")     # retry-safe: ta sama treść → ten sam plik
    return f"Zapisano: {target.name}"


TOOLS = [get_time, save_note]


def has_sensitive(tool_calls) -> bool:
    """Czy paczka wywołań zawiera choć jedno narzędzie wrażliwe."""
    return any(call["name"] in SENSITIVE for call in tool_calls)