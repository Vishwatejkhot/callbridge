"""Runtime configuration. Every secret comes from the environment (AWS Secrets Manager in prod)."""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CALLBRIDGE_", extra="ignore")

    env: str = "dev"  # dev | staging | production
    database_url: str = "sqlite:///./callbridge.db"
    # Cross-clinic jobs (improvement loop, retention purge) connect as the BYPASSRLS ops role.
    ops_database_url: str | None = None
    public_base_url: str = "http://localhost:8000"
    jwt_secret: str = "dev-only-insecure-secret-change-me-in-every-env"
    jwt_ttl_minutes: int = 12 * 60

    # LLMs. provider "rules" runs fully offline (CI, local dev, evals).
    llm_provider: str = "rules"  # rules | anthropic
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "low"  # voice turns are latency-sensitive
    openai_model: str = "gpt-5"
    groq_model: str = "llama-3.1-8b-instant"
    llm_timeout_s: float = 4.0

    # Provider keys use the providers' standard env var names (CALLBRIDGE_-prefixed also accepted).
    anthropic_api_key: str | None = Field(None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "CALLBRIDGE_ANTHROPIC_API_KEY"))
    openai_api_key: str | None = Field(None, validation_alias=AliasChoices("OPENAI_API_KEY", "CALLBRIDGE_OPENAI_API_KEY"))
    groq_api_key: str | None = Field(None, validation_alias=AliasChoices("GROQ_API_KEY", "CALLBRIDGE_GROQ_API_KEY"))
    deepgram_api_key: str | None = Field(None, validation_alias=AliasChoices("DEEPGRAM_API_KEY", "CALLBRIDGE_DEEPGRAM_API_KEY"))
    cartesia_api_key: str | None = Field(None, validation_alias=AliasChoices("CARTESIA_API_KEY", "CALLBRIDGE_CARTESIA_API_KEY"))
    cartesia_voice_id: str = "71a7ad14-091c-4e8e-a314-022ece01c121"  # British reading lady
    twilio_account_sid: str | None = Field(None, validation_alias=AliasChoices("TWILIO_ACCOUNT_SID", "CALLBRIDGE_TWILIO_ACCOUNT_SID"))
    twilio_auth_token: str | None = Field(None, validation_alias=AliasChoices("TWILIO_AUTH_TOKEN", "CALLBRIDGE_TWILIO_AUTH_TOKEN"))
    twilio_sms_from: str | None = None

    # Observability
    sentry_dsn: str | None = Field(None, validation_alias=AliasChoices("SENTRY_DSN", "CALLBRIDGE_SENTRY_DSN"))
    langfuse_enabled: bool = False

    # Guardrails
    daily_cost_cap_gbp: float = 20.0
    rate_limit_per_minute: int = 120
    enable_sim_api: bool = True  # text-only call simulation, used by load tests; off in production
    recording_retention_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
