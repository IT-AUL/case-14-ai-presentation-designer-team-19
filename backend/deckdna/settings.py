"""Runtime settings (env-driven; secrets via env or session leases only)."""

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from deckdna.provider_options import ChatOptions


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DECKDNA_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://deckdna:deckdna@localhost:5432/deckdna"
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: str = "./artifacts"

    provider_base_url: str = ""
    provider_api_key: str = ""
    # Operator-only escape hatch for a trusted local provider stub. Never
    # accept this choice from a provider-session API request.
    provider_allow_private_networks: bool = False
    provider_chat_options: ChatOptions = ChatOptions()
    # JSON of provider-specific body fields, e.g. OpenRouter routing
    # DECKDNA_PROVIDER_EXTRA_BODY='{"provider":{"sort":"throughput"}}'
    provider_extra_body: dict[str, Any] = {}
    # retries on 429/5xx (Retry-After honoured); raise for low-TPM tiers
    provider_max_retries: int = 2
    model_text: str = ""
    model_vision: str = ""
    model_embed: str = ""
    model_image: str = ""
    mock_provider: bool = True

    max_upload_mb: int = 200
    job_timeout_s: int = 600
    max_parallel_variants: int = 3
    official_mode: bool = True

    soffice_path: str = "soffice"
    pdftoppm_path: str = "pdftoppm"
    fixtures_dir: str = "./dop-data"

    secret_lease_ttl_s: int = 14400


settings = Settings()
