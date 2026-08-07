"""Agent configuration loaded from environment variables (COS_AGENT_ prefix)."""

from __future__ import annotations

import uuid

from common.utils import load_or_create_secret
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COS_AGENT_", env_file=".env", extra="ignore")

    controller_url: str = "http://127.0.0.1:8090"
    controller_api_key: str = ""
    node_id: str = ""
    ws_port: int = 8091
    heartbeat_interval_seconds: int = 30
    libvirt_uri: str = "qemu:///system"
    vm_bridge: str = "nos-br"

    def model_post_init(self, __context: object) -> None:
        if not self.node_id:
            object.__setattr__(
                self,
                "node_id",
                load_or_create_secret(
                    "/opt/cos/node_id",
                    generator=lambda: str(uuid.uuid4()),
                    gen_args=(),
                ),
            )


settings = AgentSettings()
