"""Model gateway — frozen interface, real and mock implementations."""

from deckdna.providers.base import ModelGateway, json_schema_of
from deckdna.providers.factory import build_gateway
from deckdna.providers.mock import MockProvider
from deckdna.providers.openai_compat import OpenAICompatibleProvider
from deckdna.providers.vk_inference import VKInferenceProvider

__all__ = [
    "ModelGateway",
    "MockProvider",
    "OpenAICompatibleProvider",
    "VKInferenceProvider",
    "build_gateway",
    "json_schema_of",
]
