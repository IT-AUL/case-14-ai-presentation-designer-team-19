"""Stage runner primitives — frozen interface.

Stage handlers register here; the runner owns queueing and persistence.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from deckdna.errors import DeckDNAError


@dataclass
class StageResult:
    status: str  # completed | failed | skipped
    output_artifact_ids: list[str] = field(default_factory=list)
    entity_updates: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    failure: DeckDNAError | None = None


class StageContext(Protocol):
    job_id: str
    attempt: int

    async def emit_event(
        self, type: str, message: str, progress: float | None = None, **data: Any
    ) -> None: ...


StageHandler = Callable[[StageContext], Awaitable[StageResult]]

_STAGE_REGISTRY: dict[str, StageHandler] = {}


def register_stage(name: str, handler: StageHandler) -> None:
    if name in _STAGE_REGISTRY:
        raise ValueError(f"stage already registered: {name}")
    _STAGE_REGISTRY[name] = handler


def registered_stages() -> dict[str, StageHandler]:
    return dict(_STAGE_REGISTRY)
