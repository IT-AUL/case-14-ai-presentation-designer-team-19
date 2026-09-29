"""Gateway selection — ``settings.mock_provider`` flips mock vs. real.

``DECKDNA_MOCK_PROVIDER=true`` (the default) routes every gateway call to
the deterministic offline MockProvider; unset it and configure
``DECKDNA_PROVIDER_BASE_URL`` / ``DECKDNA_PROVIDER_API_KEY`` /
``DECKDNA_MODEL_*`` to hit a real OpenAI-compatible endpoint.
"""

from __future__ import annotations

from deckdna.errors import DeckDNAError
from deckdna.providers.base import ModelGateway
from deckdna.providers.mock import MockProvider
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from deckdna.settings import Settings, settings


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
    return OpenAICompatibleProvider(
        base_url=config.provider_base_url,
        api_key=config.provider_api_key,
        model_text=config.model_text,
        model_vision=config.model_vision,
        model_embed=config.model_embed,
        model_image=config.model_image,
        chat_options=config.provider_chat_options,
        extra_body=config.provider_extra_body,
        max_retries=config.provider_max_retries,
    )
