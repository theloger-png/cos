"""Controller configuration loaded from environment variables (COS_ prefix)."""

from __future__ import annotations

from common.utils import load_or_create_secret
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://cos:cos@localhost/cos"
    secret_key: str = ""
    api_host: str = "0.0.0.0"
    api_port: int = 8090
    agent_heartbeat_timeout_seconds: int = 90

    def model_post_init(self, __context: object) -> None:
        if not self.secret_key:
            object.__setattr__(
                self,
                "secret_key",
                load_or_create_secret("/opt/cos/secret_key"),
            )


settings = Settings()
