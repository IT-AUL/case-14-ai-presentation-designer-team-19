"""Generation configuration — slide-count bounds and run-time limits.

TZ baseline is 10–15 slides, but the customer explicitly allows
user-defined lengths, so bounds are configuration data — never
constants inside validators. `configs/generation.default.yaml` ships
the official 10–15 profile; a wider profile (or per-run override)
enables shorter/longer decks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from deckdna.errors import DeckDNAError

# Absolute sanity guard rails. Mirrors schemas/deck-plan.schema.json
# (brief.target_slide_count minimum 3 / maximum 40). These are schema
# invariants, not the 10–15 product range.
ABSOLUTE_MIN_SLIDES = 3
ABSOLUTE_MAX_SLIDES = 40

# backend/deckdna/planning/config.py → <repo>/configs/ — резолвится от
# расположения пакета, а не CWD процесса (pip install . ставит CLI
# вне корня репозитория; тот же паттерн, что _SCHEMAS_DIR в validation.py).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "configs" / "generation.default.yaml"


class DeckBounds(BaseModel):
    default_slide_count: int = 12
    min_slides: int = 10
    max_slides: int = 15

    @model_validator(mode="after")
    def _check_bounds(self) -> DeckBounds:
        if self.min_slides < ABSOLUTE_MIN_SLIDES or self.max_slides > ABSOLUTE_MAX_SLIDES:
            raise ValueError(
                f"slide bounds {self.min_slides}–{self.max_slides} exceed schema "
                f"guard rails {ABSOLUTE_MIN_SLIDES}–{ABSOLUTE_MAX_SLIDES}"
            )
        if self.min_slides > self.max_slides:
            raise ValueError("min_slides > max_slides")
        if not (self.min_slides <= self.default_slide_count <= self.max_slides):
            raise ValueError("default_slide_count outside min/max")
        return self


class GenerationConfig(BaseModel):
    """Run-time generation profile. `official_mode=True` pins the TZ
    profile (3 variants, 10–15 slides, all audits)."""

    version: str = "1"
    official_mode: bool = True
    deck: DeckBounds = Field(default_factory=DeckBounds)
    protected_slide_indices: list[int] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    def slide_count_allowed(self, n: int) -> bool:
        return self.deck.min_slides <= n <= self.deck.max_slides

    def require_slide_count(self, n: int) -> None:
        if not (ABSOLUTE_MIN_SLIDES <= n <= ABSOLUTE_MAX_SLIDES):
            raise DeckDNAError(
                code="invalid_input",
                message=(
                    f"slide count {n} outside schema guard rails "
                    f"{ABSOLUTE_MIN_SLIDES}–{ABSOLUTE_MAX_SLIDES}"
                ),
                stage="planning",
                details={"slide_count": n},
            )
        if not self.slide_count_allowed(n):
            raise DeckDNAError(
                code="invalid_input",
                message=(
                    f"slide count {n} outside configured range "
                    f"{self.deck.min_slides}–{self.deck.max_slides}; adjust "
                    "deck.min_slides/deck.max_slides in the generation config "
                    "to allow non-standard lengths"
                ),
                stage="planning",
                details={
                    "slide_count": n,
                    "min_slides": self.deck.min_slides,
                    "max_slides": self.deck.max_slides,
                },
            )


def load_generation_config(path: str | Path = DEFAULT_CONFIG_PATH) -> GenerationConfig:
    """Load a generation profile from YAML. Unknown keys are preserved in
    `raw` so forward-compatible config files do not break older builds."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    deck_data = data.get("deck", {})
    return GenerationConfig(
        version=str(data.get("version", "1")),
        official_mode=bool(data.get("official_mode", True)),
        deck=DeckBounds(**deck_data),
        protected_slide_indices=list(data.get("protected_slide_indices", [])),
        raw=data,
    )
