"""Application settings loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value can be overridden via environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"

    database_url: str = "sqlite:///./data/flowforge.db"
    workspace_dir: str = "./data/workspaces"
    host_workspace_dir: str | None = None
    sandbox_image: str = "flowforge-sandbox:latest"
    shell_whitelist: str = "ls,cat,grep,find,pytest,npm,npx,node,python,python3,pip,git,make"
    cors_origins: str = "http://localhost:5173"

    github_token: str = ""

    default_max_turns: int = Field(default=20, ge=1)

    @property
    def shell_whitelist_set(self) -> frozenset[str]:
        """Whitelisted shell executables as a set."""
        return frozenset(c.strip() for c in self.shell_whitelist.split(",") if c.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
