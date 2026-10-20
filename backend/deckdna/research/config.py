"""Настройки стадии добора контента (ADR-030) из generation-конфига.

``configs/generation.default.yaml`` → секции ``content_fill`` и
``research``; адрес поиска — из окружения
(``DECKDNA_RESEARCH_SEARCH_URL``), потому что он зависит от развёртывания,
а не от профиля генерации.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deckdna.planning.config import GenerationConfig, load_generation_config


@dataclass(frozen=True)
class WebResearchConfig:
    enabled: bool = True
    search_url: str = ""
    max_queries: int = 5
    results_per_query: int = 4
    max_pages: int = 8
    page_timeout_s: float = 5.0
    max_page_bytes: int = 1500 * 1024
    max_page_chars: int = 6000
    max_facts_per_page: int = 6

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.search_url)


@dataclass(frozen=True)
class ModelFillConfig:
    enabled: bool = True
    bullets_per_section: int = 4


@dataclass(frozen=True)
class ResearchConfig:
    default_fill: str = "expand"
    budget_s: float = 30.0
    web: WebResearchConfig = field(default_factory=WebResearchConfig)
    model: ModelFillConfig = field(default_factory=ModelFillConfig)


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def load_research_config(
    config: GenerationConfig | None = None, search_url: str | None = None
) -> ResearchConfig:
    cfg = config or load_generation_config()
    if search_url is None:
        from deckdna.settings import settings

        search_url = settings.research_search_url
    research = _section(cfg.raw, "research")
    web = _section(research, "web")
    model = _section(research, "model")
    return ResearchConfig(
        default_fill=str(_section(cfg.raw, "content_fill").get("default", "expand")),
        budget_s=min(30.0, max(0.0, float(research.get("budget_s", 30)))),
        web=WebResearchConfig(
            enabled=bool(web.get("enabled", True)),
            search_url=(
                search_url
                if (search_url or "").endswith("://")
                else (search_url or "").rstrip("/")
            ),
            max_queries=int(web.get("max_queries", 5)),
            results_per_query=int(web.get("results_per_query", 4)),
            max_pages=int(web.get("max_pages", 8)),
            page_timeout_s=float(web.get("page_timeout_s", 5)),
            max_page_bytes=max(1500 * 1024, int(web.get("max_page_bytes", 1500 * 1024))),
            max_page_chars=int(web.get("max_page_chars", 6000)),
            max_facts_per_page=int(web.get("max_facts_per_page", 6)),
        ),
        model=ModelFillConfig(
            enabled=bool(model.get("enabled", True)),
            bullets_per_section=int(model.get("bullets_per_section", 4)),
        ),
    )
