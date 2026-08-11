from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"]
    log_level: str = "INFO"
    debug: bool = False
    cors_allowed_origins: list[str] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    return Settings()
