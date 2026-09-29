"""VK Inference adapter — mandatory provider for top-10 teams (TZ).

VK exposes an OpenAI-compatible inference endpoint for the supplied
model (TZ names it «Qwen 3.8 27b»). The adapter is a thin preset over
OpenAICompatibleProvider: same interface, VK-specific defaults and
env wiring. Endpoint URL is not yet published — set it via
DECKDNA_VK_BASE_URL when organizers release the spec.

Live smoke test: `DECKDNA_VK_LIVE=1` + `DECKDNA_VK_BASE_URL` +
`DECKDNA_VK_API_KEY` → tests/providers/test_vk_adapter.py runs a real
completion. Until then the mock-transport unit tests cover the
request contract.
"""

from __future__ import annotations

import os

from deckdna.providers.openai_compat import OpenAICompatibleProvider

DEFAULT_VK_MODEL = "qwen3.8-27b"  # TZ: «модель Qwen 3.8 27b»; adjust to the
# exact model id when the official endpoint spec arrives.


class VKInferenceProvider(OpenAICompatibleProvider):
    """OpenAI-compatible adapter pinned to the VK Inference endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        model: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            base_url=base_url or os.environ.get("DECKDNA_VK_BASE_URL", ""),
            api_key=api_key or os.environ.get("DECKDNA_VK_API_KEY", ""),
            model_text=model or os.environ.get("DECKDNA_VK_MODEL", DEFAULT_VK_MODEL),
            model_vision=os.environ.get("DECKDNA_VK_MODEL_VISION", ""),
            model_embed=os.environ.get("DECKDNA_VK_MODEL_EMBED", ""),
            model_image=os.environ.get("DECKDNA_VK_MODEL_IMAGE", ""),
            **kwargs,
        )
