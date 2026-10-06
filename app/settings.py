"""Runtime settings read from environment variables (and .env for local dev)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    gemini_api_key: str = ""
    gemini_model_fast: str = "gemini-3.5-flash-lite"
    gemini_model_strong: str = "gemini-3.5-flash-lite"
    gemini_model_fallback: str = "gemini-3.1-flash-lite,gemini-3-flash-preview"
    gemini_model_fast_fallback: str = ""
    live_timeout_s: float = 4.0
    gemini_model_judge: str = "gemma-4-31b-it"
    judge_sample_rate: float = 0.05
    llm_max_concurrency: int = 4
    llm_rpm: int = 0
    llm_timeout_s: float = 60.0
    llm_max_attempts: int = 4
    llm_cache_dir: Path = ROOT / "data" / "cache"
    llm_cache_enabled: bool = True

    database_url: str = "postgresql://convo:convo@localhost:5434/convo"
    redis_url: str = "redis://localhost:6379/0"

    api_key: str = ""
    config_dir: Path = ROOT / "config"
    pii_backend: str = "regex"
    sentiment_backend: str = "lexicon"
    pii_ttl_days: int = 30

    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.gemini_model_fallback.split(",") if m.strip()]

    @property
    def fast_fallback_models(self) -> list[str]:
        chain = [m.strip() for m in self.gemini_model_fast_fallback.split(",") if m.strip()]
        return chain or self.fallback_models

    @property
    def asyncpg_dsn(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
