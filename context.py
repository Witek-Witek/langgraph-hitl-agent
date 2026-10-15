from dataclasses import dataclass


@dataclass(frozen=True)
class RunContext:
    """Parametry wyboru per uruchomienie. Wyłącznie KLUCZE katalogów —
    rozwiązanie klucza na specyfikację dzieje się po stronie serwera."""

    profile: str = ""        # "" = domyślny z LLM_DEFAULT_PROFILE
    persona: str = ""        # "" = domyślna z LLM_DEFAULT_PERSONA