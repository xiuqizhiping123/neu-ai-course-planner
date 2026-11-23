from pathlib import Path

from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )
    planner_model: str = "deepseek-chat"
    openai_api_key: SecretStr
    openai_base_url: str | None = "https://api.deepseek.com"
    max_tokens: int | None = None
    request_timeout: float = 60.0
    max_retries: int = 2


def create_model(
    settings: ModelSettings | None = None,
    *,
    temperature: float = 0.0,
    request_timeout: float | None = None,
    max_retries: int | None = None,
) -> ChatOpenAI:
    cfg = settings if settings is not None else ModelSettings()  # type: ignore[call-arg]
    return ChatOpenAI(
        model=cfg.planner_model,
        api_key=cfg.openai_api_key,
        base_url=cfg.openai_base_url,
        temperature=temperature,
        max_completion_tokens=cfg.max_tokens,
        timeout=request_timeout if request_timeout is not None else cfg.request_timeout,
        max_retries=max_retries if max_retries is not None else cfg.max_retries,
    )
