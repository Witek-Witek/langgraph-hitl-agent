"""Diagnostyka aplikacji — uruchamiana RĘCZNIE: uv run python diagnostics.py

Uzupełnia banner startowy (llm.config_banner): banner mówi CO skonfigurowano,
diagnostyka sprawdza, CZY to działa. Wykonuje realne, płatne wywołania —
nie wpinać pod endpoint ani w ścieżkę startową.
"""

import os
import sys
import time
from typing import Callable, Literal
from langchain_core.exceptions import LangChainException, OutputParserException

Level = Literal["OK", "WARN", "FAIL"]
Result = tuple[Level, list[str]]

_ORDER = {"OK": 0, "WARN": 1, "FAIL": 2}
_MARK = {"OK": "OK  ", "WARN": "WARN", "FAIL": "FAIL"}

# Prefiks specyfikacji → zmienna z kluczem (rozszerzać przy nowych backendach)
PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "CUSTOM_API_KEY",
}



def _llm_for(key: str):
    """Klucz z describe_config() → instancja modelu. 'rola:main' | 'profil:fast'."""
    from llm import get_profile_llm, get_role_llm

    kind, _, name = key.partition(":")
    if kind == "profil":
        return get_profile_llm(name)
    if kind == "rola":
        return get_role_llm(name)
    raise ValueError(f"Nieznany typ wpisu konfiguracji: {key!r}")

# Role, które MUSZĄ wspierać tool-calling -> sprawdzenie structured_output i tool_choice
ROLES_REQUIRING_TOOLS = {"profil:fast", "profil:balanced", "profil:deep", "rola:main", "rola:distiller", "rola:classifier"}


def _worst(a: Level, b: Level) -> Level:
    return a if _ORDER[a] >= _ORDER[b] else b


def _cap(level: Level, ceiling: Level) -> Level:
    return level if _ORDER[level] <= _ORDER[ceiling] else ceiling


# ---------------------------------------------------------------- checks

def check_env() -> Result:
    from llm import ROLE_ENV, describe_config

    lines: list[str] = []
    level: Level = "OK"

    for var in ("DATABASE_URL", "WORKSPACE_DIR", "SUPABASE_URL", *ROLE_ENV.values()):
        if not os.environ.get(var):
            lines.append(f"brak {var}")
            level = "FAIL"

    sb = os.environ.get("SUPABASE_URL", "")
    if sb and not sb.startswith("https://"):
        lines.append("SUPABASE_URL nie zaczyna się od https://")
        level = "FAIL"

    uri = os.environ.get("DATABASE_URL", "")
    if uri and not uri.startswith("postgresql://"):
        lines.append("DATABASE_URL nie zaczyna się od postgresql://")
        level = "FAIL"

    if level != "FAIL":
        for role, spec in describe_config().items():
            provider = spec.split(":", 1)[0]
            key_var = PROVIDER_KEY_ENV.get(provider)
            if key_var is None:
                lines.append(f"{role}: nieznany prefiks '{provider}' — brak mapowania klucza")
                level = _worst(level, "WARN")
            elif not os.environ.get(key_var):
                lines.append(f"{role}: spec '{provider}:' wymaga {key_var} (brak)")
                level = "FAIL"

    if not lines:
        lines.append("wszystkie wymagane zmienne obecne")
    return level, lines


def check_database() -> Result:
    import psycopg

    uri = os.environ.get("DATABASE_URL")
    if not uri:
        return "FAIL", ["brak DATABASE_URL"]

    t0 = time.perf_counter()
    try:
        with psycopg.connect(uri, connect_timeout=10) as conn:
            conn.execute("select 1").fetchone()
            row = conn.execute(
                "select count(*) from information_schema.tables "
                "where table_name = 'checkpoints'"
            ).fetchone()
        dt = time.perf_counter() - t0
        tables = "są" if (row and row[0]) else "brak (utworzy setup() przy starcie)"
        return "OK", [f"połączenie OK ({dt:.2f}s), tabele checkpointera: {tables}"]
    except Exception as e:
        return "FAIL", [f"{type(e).__name__} — sprawdź DATABASE_URL"]


def check_auth_jwks() -> Result:
    from jwt import PyJWKClient
    from jwt.exceptions import PyJWKClientError, PyJWKSetError

    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not base:
        return "FAIL", ["brak SUPABASE_URL"]
    allowed = {"ES256", "RS256"}
    try:
        jwk_set = PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json", timeout=5).get_jwk_set()
    except (PyJWKClientError, PyJWKSetError) as e:
        return "FAIL", [f"JWKS nieosiągalny lub pusty: {type(e).__name__}"]

    algs = sorted({k.algorithm_name for k in jwk_set.keys})
    if not allowed & set(algs):
        return "FAIL", [f"brak klucza z dozwolonej listy {sorted(allowed)}; jest: {algs} (E-5)"]
    return "OK", [f"{len(jwk_set.keys)} klucz(e), algorytmy: {algs}"]


def check_db_threads_table() -> Result:
    import psycopg
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
            row = conn.execute("select to_regclass('public.threads')").fetchone()
    except Exception as e:
        return "FAIL", [f"{type(e).__name__} — sprawdź DATABASE_URL"]
    if not row or row[0] is None:
        return "FAIL", ["tabela threads nie istnieje — uruchom sql/001_threads_and_rls.sql"]
    return "OK", ["tabela threads istnieje"]


def check_db_rls_public() -> Result:
    import psycopg
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
            rows = conn.execute(
                "select c.relname from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' and c.relkind in ('r', 'p') "
                "and not c.relrowsecurity order by c.relname"
            ).fetchall()
    except Exception as e:
        return "FAIL", [f"{type(e).__name__} — sprawdź DATABASE_URL"]
    if rows:
        return "FAIL", [f"tabele w public BEZ RLS: {', '.join(r[0] for r in rows)}"]
    return "OK", ["wszystkie tabele w public mają RLS"]


def check_workspace() -> Result:
    try:
        from tools import workspace_dir
        return "OK", [f"{workspace_dir()} — zapis potwierdzony"]
    except Exception as e:
        return "FAIL", [f"{type(e).__name__}: {e}"]


def probe_tool_support(role: str) -> tuple[Level, str, float]:
    """(poziom, komunikat, latencja). Rozróżnia błąd wywołania od braku zdolności."""
    from langchain_core.tools import tool

    @tool
    def _ping(x: str) -> str:
        """Zwraca podany tekst. Użyj tego narzędzia zawsze."""
        return x

    t0 = time.perf_counter()
    try:
        msg = _llm_for(role).bind_tools([_ping]).invoke("Użyj narzędzia _ping z x='ok'.")
    except Exception as e:
        return "FAIL", f"BŁĄD: {type(e).__name__} (model nieosiągalny lub zła nazwa)", time.perf_counter() - t0

    dt = time.perf_counter() - t0
    if getattr(msg, "tool_calls", None):
        return "OK", "tool_calls: TAK", dt
    if role in ROLES_REQUIRING_TOOLS:
        return "FAIL", "tool_calls: NIE — rola wymaga narzędzi!", dt
    return "OK", "tool_calls: NIE (rola ich nie wymaga)", dt


def probe_tool_choice(role: str) -> tuple[Level, str, float]:
    """Czy backend respektuje wymuszenie wywołania narzędzia."""
    from langchain_core.tools import tool

    @tool
    def _ping(x: str) -> str:
        """Zwraca podany tekst."""
        return x

    t0 = time.perf_counter()
    try:
        # Pytanie CELOWO niezwiązane z narzędziem — bez wymuszenia model odpowie tekstem
        msg = _llm_for(role).bind_tools([_ping], tool_choice="any").invoke(
            "Jaka jest stolica Francji?"
        )
    except (OutputParserException, LangChainException) as e:
        # Przechwyci specyficzne błędy LangChain (np. nieobsługiwany parametr tool_choice przez dany model)
        return "WARN", f"tool_choice: błąd integracji frameworka ({type(e).__name__}: {e})", time.perf_counter() - t0
    except Exception as e:
        # Przechwyci błędy sieciowe, brak klucza API, timeouty itp.
        return "WARN", f"Błąd infrastruktury/API ({type(e).__name__}: {e})", time.perf_counter() - t0

    dt = time.perf_counter() - t0
    if getattr(msg, "tool_calls", None):
        return "OK", f"tool_choice: respektowane", dt
    return "WARN", "tool_choice: IGNOROWANE (model odpowiedział tekstem mimo wymuszenia)", dt


def probe_structured_output(role: str) -> tuple[Level, str, float]:
    """Czy backend zwraca odpowiedź w wymuszonym kształcie."""
    from pydantic import BaseModel, Field

    class _Answer(BaseModel):
        """Stolica i kraj."""
        city: str = Field(description="Nazwa miasta")
        country: str = Field(description="Nazwa kraju")

    t0 = time.perf_counter()
    try:
        result = _llm_for(role).with_structured_output(_Answer).invoke("Podaj stolicę Niemiec.")
    except Exception as e:
        return "WARN", f"structured_output: nieobsługiwane ({type(e).__name__})", time.perf_counter() - t0

    dt = time.perf_counter() - t0
    if isinstance(result, _Answer) and result.city:
        return "OK", f"structured_output: OK ({result.city} / {result.country})", dt
    return "WARN", f"structured_output: nieoczekiwany kształt ({type(result).__name__})", dt


def check_models() -> Result:
    from llm import describe_config

    lines: list[str] = []
    level: Level = "OK"
    for role, spec in describe_config().items():
        lvl, msg, dt = probe_tool_support(role)
        lines.append(f"{role:<12} {spec:<45} {msg}  ({dt:.2f}s)")
        level = _worst(level, lvl)

        if lvl == "OK" and role in ROLES_REQUIRING_TOOLS:
            lvl2, msg2, dt2 = probe_tool_choice(role)
            lines.append(f"{'':<12} {'':<45} {msg2}  ({dt2:.2f}s)")
            level = _worst(level, lvl2)

        if lvl == "OK":
            lvl3, msg3, dt3 = probe_structured_output(role)
            lines.append(f"{'':<12} {'':<45} {msg3}  ({dt3:.2f}s)")
            level = _worst(level, lvl3)
        
    return level, lines


def check_langsmith() -> Result:
    if os.environ.get("LANGSMITH_TRACING", "").lower() != "true":
        return "WARN", ["tracing wyłączony (LANGSMITH_TRACING != true)"]
    if not os.environ.get("LANGSMITH_API_KEY"):
        return "WARN", ["TRACING=true, ale brak LANGSMITH_API_KEY"]
    try:
        from langsmith import Client
        t0 = time.perf_counter()
        list(Client().list_projects(limit=1))
        return "OK", [f"projekt: {os.environ.get('LANGSMITH_PROJECT', '?')} "
                      f"({time.perf_counter() - t0:.2f}s)"]
    except Exception as e:
        return "WARN", [f"{type(e).__name__} — sprawdź klucz i LANGSMITH_ENDPOINT (region EU!)"]


# ---------------------------------------------------------------- runner

CHECKS: list[tuple[str, Callable[[], Result], Level]] = [
    ("Konfiguracja env",          check_env,              "FAIL"),
    ("Połączenie z bazą",         check_database,         "FAIL"),
    ("Tabela rejestru wątków",    check_db_threads_table, "FAIL"),
    ("RLS na schemacie public",   check_db_rls_public,    "FAIL"),
    ("Klucze weryfikacji (JWKS)", check_auth_jwks,        "FAIL"),
    ("Katalog roboczy narzędzi",  check_workspace,        "FAIL"),
    ("Zdolności modeli",          check_models,           "FAIL"),
    ("Tracing",                   check_langsmith,        "WARN"),
]


def run_all() -> int:
    from llm import config_banner
    print("=== DIAGNOSTYKA ===\n")
    print(config_banner(), "\n")

    worst: Level = "OK"
    for name, func, ceiling in CHECKS:
        try:
            level, lines = func()
        except Exception as e:
            level, lines = "FAIL", [f"check padł: {type(e).__name__}: {e}"]
        level = _cap(level, ceiling)
        worst = _worst(worst, level)
        print(f"[{_MARK[level]}] {name}")
        for line in lines:
            print(f"       {line}")
        print()

    print(f"=== WYNIK: {worst} ===")
    return 1 if worst == "FAIL" else 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    sys.exit(run_all())