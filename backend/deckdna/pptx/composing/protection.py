"""Protected-slide guard for the constraint compiler and repair.

Certain slide indices (e.g. mandatory submission slides 7–11 in the
LCT template) must survive generation and repair byte-identical:
no text edits, no geometry changes, no exemplar swaps, no removal or
reordering. The guard is the single choke point every mutation path
must pass through — compose, repair, and exemplar swap all call it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from deckdna.errors import DeckDNAError

MUTATING_ACTIONS = frozenset(
    {
        "shorten_text",
        "rewrite_title",
        "split_slide",
        "merge_slide",
        "swap_exemplar",
        "move_shape",
        "resize_shape",
        "align_shapes",
        "map_font",
        "map_color",
        "recrop_image",
        "replace_visual",
        "clone_group",
        "remove_group",
        "add_chart_metadata",
        "remove_placeholder",
        "native_rebuild",
    }
)


class ProtectedSlides:
    """Index set that rejects every mutation on protected slides."""

    def __init__(self, indices: Iterable[int] = ()) -> None:
        self._indices = frozenset(indices)

    @property
    def indices(self) -> frozenset[int]:
        return self._indices

    def is_protected(self, slide_index: int) -> bool:
        return slide_index in self._indices

    def check(self, slide_index: int, operation: str, *, stage: str = "pptx") -> None:
        """Raise `protected_slide` when `operation` targets a protected slide."""
        if slide_index in self._indices:
            raise DeckDNAError(
                code="protected_slide",
                message=(
                    f"slide {slide_index} is protected: '{operation}' rejected "
                    "(no text, geometry, exemplar-swap or structural mutation allowed)"
                ),
                stage=stage,
                details={"slide_index": slide_index, "operation": operation},
            )

    def guard_action(
        self, action: dict[str, Any], slide_index_of: dict[str, int], *, stage: str = "repair"
    ) -> None:
        """Validate a RepairAction dict against the protected set.

        `slide_index_of` maps slide_id -> slide index (0-based, DeckPlan order).
        `reorder_slides` is rejected wholesale while any slide is protected,
        because moving slides changes protected indices' positions.
        """
        action_type = action.get("action_type", "")
        if action_type == "reorder_slides" and self._indices:
            raise DeckDNAError(
                code="protected_slide",
                message="reorder_slides rejected: protected slides must keep position",
                stage=stage,
                details={"protected": sorted(self._indices)},
            )
        if action_type not in MUTATING_ACTIONS:
            return
        slide_id = (action.get("target") or {}).get("slide_id", "")
        index = slide_index_of.get(slide_id)
        if index is not None:
            self.check(index, action_type, stage=stage)

    def filter_actions(
        self, actions: list[dict[str, Any]], slide_index_of: dict[str, int]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split repair actions into (allowed, rejected) without raising —
        for the UI "select issues to fix" flow where protected hits should
        be reported, not crash the batch."""
        allowed: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for action in actions:
            try:
                self.guard_action(action, slide_index_of)
            except DeckDNAError:
                rejected.append(action)
            else:
                allowed.append(action)
        return allowed, rejected
