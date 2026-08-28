"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    #: "rule" (offline, deterministic) or "llm" (Claude, falls back to rule).
    extractor: str = "rule"
    anthropic_api_key: str | None = None
    model: str = "claude-sonnet-5"
    max_prompt_chars: int = 4000
    cors_origins: tuple[str, ...] = ("*",)
    debug: bool = False

    @property
    def llm_enabled(self) -> bool:
        return self.extractor == "llm" and bool(self.anthropic_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    key = os.getenv("ANTHROPIC_API_KEY") or None
    extractor = os.getenv("EXTRACTOR", "rule").strip().lower()
    if extractor not in ("rule", "llm"):
        extractor = "rule"
    origins = os.getenv("CORS_ORIGINS", "*")
    return Settings(
        extractor=extractor,
        anthropic_api_key=key,
        model=os.getenv("LLM_MODEL", "claude-sonnet-5"),
        max_prompt_chars=int(os.getenv("MAX_PROMPT_CHARS", "4000")),
        cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
        debug=_bool("DEBUG"),
    )
