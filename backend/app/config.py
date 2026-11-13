"""Application settings loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value can be overridden via environment."""

    # Reads the repo-root .env too, so `uvicorn` started from backend/ sees the same config as compose.
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: str = ""
    # Server-wide fallback keys for users who haven't stored their own.
    openai_api_key: str = ""
    # Pricing table (USD per 1M tokens) used for cost estimates; editable via API.
    pricing_file: str = "./data/pricing.json"
    anthropic_model: str = "claude-opus-5-5"
    # Server-side refusal fallbacks (Claude API only). Disable on Bedrock/Vertex.
    anthropic_fallbacks: bool = True
    # Effort level: low | medium | high | xhigh | max. Empty = model default.
    anthropic_effort: str = "medium"

    database_url: str = "sqlite:///./data/flowforge.db"
    workspace_dir: str = "./data/workspaces"
    host_workspace_dir: str | None = None
    sandbox_image: str = "flowforge-sandbox:latest"
    # Default network for sandboxes: none | allowlist | bridge. Flows may choose
    # per flow; the server caps below bound what users can request.
    sandbox_network: str = "none"
    # Highest network mode a flow may request: none < allowlist < bridge.
    sandbox_max_network: str = "allowlist"
    sandbox_max_cpus: float = 2.0
    sandbox_max_memory_mb: int = 2048
    sandbox_max_timeout_seconds: int = 3600
    # Files under /repo/output are copied here after each run.
    artifacts_dir: str = "./data/artifacts"
    artifact_max_bytes: int = 50 * 1024 * 1024
    artifact_max_files: int = 200
    max_steps_per_run: int = Field(default=50, ge=1)
    # Run-input uploads and watched folders live here.
    uploads_dir: str = "./data/uploads"
    # Skills are materialized here (SKILL.md + files) before being copied into the sandbox.
    skills_dir: str = "./data/skills"
    watch_dir: str = "./data/watch"
    # How often the trigger scheduler checks schedules / mailboxes / folders.
    trigger_poll_seconds: int = Field(default=30, ge=5)
    # Public base URL used to display webhook URLs (defaults to the request's host).
    public_base_url: str = ""
    shell_whitelist: str = "ls,cat,grep,find,pytest,npm,npx,node,python,python3,pip,git,make"
    cors_origins: str = "http://localhost:5173"

    github_token: str = ""

    default_max_turns: int = Field(default=20, ge=1)

    # ---- auth ----
    # Secret used to sign JWTs. Empty = random per process (dev only; sessions
    # do not survive a restart).
    jwt_secret: str = ""
    # Secret used to encrypt stored API keys (Fernet). Empty = derived from jwt_secret.
    app_secret: str = ""
    access_token_minutes: int = Field(default=15, ge=1)
    refresh_token_days: int = Field(default=7, ge=1)
    # Set True behind HTTPS so cookies carry the Secure flag.
    cookie_secure: bool = False
    # Internal URL prefixes the backend may call on behalf of users (SSRF guard exemption),
    # e.g. a local Ollama. Everything else must resolve to a public address.
    trusted_internal_urls: str = (
        "http://localhost:11434,http://127.0.0.1:11434,http://host.docker.internal:11434"
    )
    # How often the industry automations are evaluated (reminders, renewals, reports).
    biz_tick_seconds: int = Field(default=60, ge=10)
    # Knowledge-base embeddings: lexical (offline, default) | openai (needs an OpenAI key).
    kb_embeddings: str = "lexical"
    # MCP servers started as a local process (stdio) run on the backend host: admin only.
    mcp_stdio_admin_only: bool = True
    # Who may create accounts: open | first_user (only while no account exists) | closed.
    registration: str = "open"
    # Billing: trial length and the bank account shown on VietQR invoices (NAPAS bank BIN).
    billing_trial_days: int = Field(default=30, ge=1)
    billing_bank_bin: str = "970436"
    billing_bank_name: str = "Vietcombank"
    billing_bank_account: str = ""
    billing_bank_account_name: str = ""
    # Operator alerts (connector down, failed automation, billing block) are POSTed here as JSON.
    alert_webhook_url: str = ""
    # Failed logins per (client IP, email) allowed inside the window before 429.
    login_max_failures: int = Field(default=10, ge=1)
    login_window_seconds: int = Field(default=900, ge=10)

    @property
    def shell_whitelist_set(self) -> frozenset[str]:
        """Whitelisted shell executables as a set."""
        return frozenset(c.strip() for c in self.shell_whitelist.split(",") if c.strip())

    @property
    def trusted_internal_urls_list(self) -> list[str]:
        return [u.strip().rstrip("/") for u in self.trusted_internal_urls.split(",") if u.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list."""
        # "*" is never allowed: the API uses credentials (cookies), and a wildcard
        # with credentials would let any site call it as the user.
        return [o.strip() for o in self.cors_origins.split(",") if o.strip() and o.strip() != "*"]


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
