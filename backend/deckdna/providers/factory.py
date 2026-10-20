"""Gateway selection — ``settings.mock_provider`` flips mock vs. real.

``DECKDNA_MOCK_PROVIDER=true`` is an explicit offline/test opt-in and routes calls to
the deterministic MockProvider. The default is false; configure
``DECKDNA_PROVIDER_BASE_URL`` / ``DECKDNA_PROVIDER_API_KEY`` /
``DECKDNA_MODEL_*`` to hit a real OpenAI-compatible endpoint.
"""

from __future__ import annotations

from deckdna.errors import DeckDNAError
from deckdna.providers.base import ModelGateway
from deckdna.providers.mock import MockProvider
from deckdna.providers.openai_compat import OpenAICompatibleProvider, shared_pacer
from deckdna.settings import Settings, settings


def provider_headers(config: Settings, project_id: str | None = None) -> dict[str, str]:
    """Дополнительные заголовки провайдера: ``Project-ID`` (VK Inference)
    и произвольные из ``provider_extra_headers``."""
    headers = dict(config.provider_extra_headers)
    project = project_id if project_id is not None else config.provider_project_id
    if project:
        headers["Project-ID"] = project
    return headers


def build_gateway(config: Settings | None = None) -> ModelGateway:
    """Return the gateway selected by ``config.mock_provider``."""
    config = config or settings
    if config.mock_provider:
        return MockProvider()
    missing = [
        name
        for name, value in (
            ("provider_base_url", config.provider_base_url),
            ("provider_api_key", config.provider_api_key),
            ("model_text", config.model_text),
        )
        if not value
    ]
    if missing:
        raise DeckDNAError(
            code="provider_capability_missing",
            message=f"mock_provider is off but settings are incomplete: {', '.join(missing)}",
            stage="providers",
            details={"missing": missing},
        )
    image_delegate = None
    if config.image_base_url and config.model_image:
        image_delegate = OpenAICompatibleProvider(
            base_url=config.image_base_url,
            api_key=config.image_api_key or config.provider_api_key,
            model_text="",
            model_image=config.model_image,
            image_transport=config.image_transport,
            max_retries=config.provider_max_retries,
        )
    vision_delegate = None
    if config.vision_base_url and config.model_vision:
        vision_delegate = OpenAICompatibleProvider(
            base_url=config.vision_base_url,
            api_key=config.vision_api_key or config.provider_api_key,
            model_text="",
            model_vision=config.model_vision,
            extra_body=config.provider_extra_body,
            max_retries=config.provider_max_retries,
            extra_headers=(
                provider_headers(config)
                if config.vision_base_url.rstrip("/") == config.provider_base_url.rstrip("/")
                else None
            ),
        )
    fallback_provider = None
    if config.fallback_provider_base_url:
        fallback_model_text = config.fallback_model_text or config.model_text
        fallback_model_vision = config.fallback_model_vision or config.model_vision
        fallback_provider = OpenAICompatibleProvider(
            base_url=config.fallback_provider_base_url,
            api_key=config.fallback_provider_api_key or config.provider_api_key,
            model_text=fallback_model_text,
            model_vision=fallback_model_vision,
            model_embed=config.model_embed,
            extra_body=config.fallback_provider_extra_body,
            extra_headers=config.fallback_provider_extra_headers,
            max_retries=config.provider_max_retries,
            min_request_interval_s=config.provider_min_request_interval_s,
            retry_base_delay_s=config.provider_retry_base_delay_s,
            pacer=shared_pacer(
                config.fallback_provider_base_url, config.provider_min_request_interval_s
            ),
        )
    return OpenAICompatibleProvider(
        fallback_provider=fallback_provider,
        image_delegate=image_delegate,
        vision_delegate=vision_delegate,
        base_url=config.provider_base_url,
        api_key=config.provider_api_key,
        model_text=config.model_text,
        model_vision=config.model_vision,
        model_embed=config.model_embed,
        model_image=config.model_image,
        chat_options=config.provider_chat_options,
        extra_body=config.provider_extra_body,
        max_retries=config.provider_max_retries,
        min_request_interval_s=config.provider_min_request_interval_s,
        retry_base_delay_s=config.provider_retry_base_delay_s,
        # один темп на endpoint для всех gateway'ев процесса: варианты
        # идут параллельно, каждый со своим gateway (Фаза 5)
        pacer=shared_pacer(config.provider_base_url, config.provider_min_request_interval_s),
        extra_headers=provider_headers(config),
    )
