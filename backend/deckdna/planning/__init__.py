"""Planning: Brief + ContentPack → DeckPlan (docs/ARCHITECTURE.md)."""

from deckdna.planning.config import GenerationConfig, load_generation_config
from deckdna.planning.story_director import plan_deck, plan_deck_llm
from deckdna.planning.validation import validate_deck_plan, validate_slide_count

__all__ = [
    "GenerationConfig",
    "load_generation_config",
    "plan_deck",
    "plan_deck_llm",
    "validate_deck_plan",
    "validate_slide_count",
]
