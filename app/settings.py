from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORTELIUS_", env_file=".env", extra="ignore")

    cors_allowed_origins: list[str] = Field(min_length=1)
    database_url: SecretStr
    draft_provider: Literal["gemini", "openrouter"] = "gemini"
    # Vazio usa o modelo padrão do provedor escolhido.
    draft_model: str | None = None
    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
