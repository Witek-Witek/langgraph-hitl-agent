from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from uuid import UUID

from llm import MODEL_PROFILES
from prompts import PERSONAS

class _WithContext(BaseModel):
    """Wspólne pola kontekstu uruchomienia. Puste = domyślne z konfiguracji serwera."""
    profile: str = Field(default="", max_length=32)
    persona: str = Field(default="", max_length=32)

    @field_validator("profile")
    @classmethod
    def _check_profile(cls, v: str) -> str:
        if v and v not in MODEL_PROFILES:
            raise ValueError(f"Nieznany profil: {v!r}")
        return v

    @field_validator("persona")
    @classmethod
    def _check_persona(cls, v: str) -> str:
        if v and v not in PERSONAS:
            raise ValueError(f"Nieznana persona: {v!r}")
        return v

class ChatRequest(_WithContext):                    # bez zmian
    thread_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=10_000)


class ResumeRequest(_WithContext):                  # NOWY
    action: Literal["approve", "reject"] = Field(..., description="Decyzja człowieka")
    note: str | None = Field(default=None, max_length=1_000)


class FinishRequest(_WithContext):
    """Bez dodatkowych pól — ciało istnieje wyłącznie dla kontekstu i jego walidacji."""


class ThreadCreate(BaseModel):
    label: str | None = Field(default=None, max_length=120)


class ThreadRename(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)


class ThreadOut(BaseModel):
    thread_id: UUID
    label: str | None
    created_at: datetime
    updated_at: datetime