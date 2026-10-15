import os
from functools import cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

ROLE_ENV = {"main": "LLM_MAIN", "classifier": "LLM_CLASSIFIER", "distiller": "LLM_DISTILLER"}
CUSTOM_PREFIX = "custom:"


# --- Katalog profili: to, z czego wybiera użytkownik ---
# Modele:  openai:gpt-4o-mini
MODEL_PROFILES = {
    "fast": {
        "spec": "openai:gpt-5-nano",
        "label": "Szybki",
        "supports_temperature": False,
    },
    "balanced": {
        "spec": "openrouter:deepseek/deepseek-chat-v3.1",
        "label": "Zbalansowany",
        "supports_temperature": True,
        "temperature": 0.2,
    },
    "deep": {
        "spec": "anthropic:claude-haiku-4-5-20251001",
        "label": "Dokładny",
        "supports_temperature": True,
        "temperature": 0.0,
    },
}


def _spec(role: str) -> str:
    """Rozwiązuje rolę na specyfikację modelu. Fail-fast, bez wartości domyślnej."""
    var = ROLE_ENV[role]
    spec = os.environ.get(var)
    if not spec:
        raise RuntimeError(f"Brak {var} w .env — ustaw specyfikację modelu (provider:model).")
    return spec


def _kwargs() -> dict:
    """Parametry opcjonalne: pusta wartość = nie przekazuj (decyzja 7)."""
    t = os.environ.get("LLM_TEMPERATURE", "").strip()
    return {"temperature": float(t)} if t else {}


def _resolve(spec: str) -> tuple[str, dict]:
    """Zwraca (spec_dla_fabryki, dodatkowe_kwargs).

    Prefiks 'custom:' kieruje endpoint OpenAI-kompatybilny bez dedykowanego pakietu
    przez adapter OpenAI, podmieniając bazowy URL i klucz.
    """
    if not spec.startswith(CUSTOM_PREFIX):
        return spec, {}

    model = spec[len(CUSTOM_PREFIX):]
    base_url = os.environ.get("CUSTOM_BASE_URL")
    api_key = os.environ.get("CUSTOM_API_KEY")
    model_provider = os.environ.get("CUSTOM_MODEL_PROVIDER", "openai")
    if not base_url or not api_key:
        raise RuntimeError(
            "Prefiks 'custom:' wymaga CUSTOM_BASE_URL i CUSTOM_API_KEY w .env."
        )
    return model, {"model_provider": model_provider, "base_url": base_url, "api_key": api_key}


def resolve_profile(key: str) -> str:
    """'' → domyślny z env; nieznany → ValueError (druga linia obrony)."""
    k = key or os.environ.get("LLM_DEFAULT_PROFILE", "")
    if k not in MODEL_PROFILES:
        raise ValueError(f"Nieznany profil modelu: {key!r}")
    return k


def public_profiles() -> list[dict]:
    """Dla klienta: klucz + etykieta. BEZ spec — nie ujawniamy dostawcy ani modelu."""
    return [{"key": k, "label": p["label"]} for k, p in MODEL_PROFILES.items()]


def _profile_kwargs(profile: dict) -> dict:
    """Temperatura jest właściwością profilu, nie globalną zmienną (E-3 ze Stage 08)."""
    if not profile.get("supports_temperature"):
        return {}
    return {"temperature": profile.get("temperature", 0.0)}


def describe_config() -> dict[str, str]:
    cfg = {f"rola:{r}": _spec(r) for r in ROLE_ENV}
    cfg.update({f"profil:{k}": p["spec"] for k, p in MODEL_PROFILES.items()})
    return cfg


def config_banner() -> str:
    """Jedna linia z rozwiązaną konfiguracją ról i katalogu profili."""
    cfg = describe_config()
    line = " | ".join(f"{k}={v}" for k, v in cfg.items())

    default = os.environ.get("LLM_DEFAULT_PROFILE", "")
    if default not in MODEL_PROFILES:
        line += f"\n   ⚠ LLM_DEFAULT_PROFILE={default!r} spoza katalogu"

    return f"[LLM] {line}"


@cache
def get_profile_llm(profile_key: str) -> BaseChatModel:
    """Model główny wg profilu. Klucz cache = klucz katalogu → cache ograniczony katalogiem."""
    profile = MODEL_PROFILES[resolve_profile(profile_key)]
    spec, extra = _resolve(profile["spec"])
    return init_chat_model(spec, **extra, **_profile_kwargs(profile))


@cache
def get_role_llm(role: str) -> BaseChatModel:
    """Model roli serwerowej — z konfiguracji operatora, nie od klienta."""
    spec, extra = _resolve(_spec(role))
    return init_chat_model(spec, **extra, **_kwargs())