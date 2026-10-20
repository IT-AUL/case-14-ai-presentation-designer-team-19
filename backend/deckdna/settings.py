"""Runtime settings (env-driven; secrets via env or session leases only)."""

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from deckdna.provider_options import ChatOptions


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DECKDNA_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://deckdna:deckdna@localhost:5432/deckdna"
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: str = "./artifacts"
    # ADR-031: снапшот состояния API в <artifact_root>/state/ после каждой
    # мутирующей операции и загрузка при старте (compose включает)
    state_snapshot: bool = False
    durable_queue: bool = False
    worker_token: str = ""

    provider_base_url: str = ""
    provider_api_key: str = ""
    # VK Inference требует заголовок Project-ID (инструкция организаторов
    # 17.10, dop-data/6. VK Inference — quickstart.md)
    provider_project_id: str = ""
    # прочие заголовки провайдера, JSON: {"X-Header": "value"}
    provider_extra_headers: dict[str, str] = {}
    # Operator-only escape hatch for a trusted local provider stub. Never
    # accept this choice from a provider-session API request.
    provider_allow_private_networks: bool = False
    provider_chat_options: ChatOptions = ChatOptions()
    # JSON of provider-specific body fields, e.g. OpenRouter routing
    # DECKDNA_PROVIDER_EXTRA_BODY='{"provider":{"sort":"throughput"}}'
    provider_extra_body: dict[str, Any] = {}
    # retries on 429/5xx (Retry-After honoured); raise for low-TPM tiers
    provider_max_retries: int = 4
    # пауза между СТАРТАМИ запросов к текстовому провайдеру и база
    # экспоненциальной задержки повтора — чтобы не долбиться в 429
    # (DECKDNA_PROVIDER_MIN_REQUEST_INTERVAL_S / _RETRY_BASE_DELAY_S)
    provider_min_request_interval_s: float = 1.0
    provider_retry_base_delay_s: float = 3.0
    # Fallback provider when primary (e.g. VK Inference) fails
    fallback_provider_base_url: str = ""
    fallback_provider_api_key: str = ""
    fallback_provider_extra_headers: dict[str, str] = {}
    fallback_provider_extra_body: dict[str, Any] = {}
    fallback_model_text: str = ""
    fallback_model_vision: str = ""
    # text-to-image at a separate provider (ADR-017): e.g. OpenRouter
    # FLUX.2 klein 4B (Apache-2.0) with image_transport="chat"
    image_base_url: str = ""
    image_api_key: str = ""
    image_transport: str = "images"
    # vision at a separate provider (e.g. text via a dev shim without image
    # input, vision via OpenRouter); model = model_vision
    vision_base_url: str = ""
    vision_api_key: str = ""
    model_text: str = ""
    model_vision: str = ""
    model_embed: str = ""
    model_image: str = ""
    # Только явный offline/test opt-in. Отсутствие ключа не включает имитацию.
    mock_provider: bool = False

    # ADR-030: SearXNG (open-source метапоиск) для веб-ресёрча при добор
    # контента; пусто — веб-поиск выключен, добор только из знаний модели
    research_search_url: str = "ddg://"
    max_upload_mb: int = 200
    job_timeout_s: int = 600
    max_parallel_variants: int = 3
    official_mode: bool = True

    soffice_path: str = "soffice"
    pdftoppm_path: str = "pdftoppm"
    fixtures_dir: str = "./dop-data"

    secret_lease_ttl_s: int = 14400


settings = Settings()
