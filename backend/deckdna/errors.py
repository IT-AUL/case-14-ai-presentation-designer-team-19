"""Typed error model shared by all layers (see docs/ARCHITECTURE.md §15)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ERROR_CODES = {
    "invalid_input",
    "not_found",
    "not_implemented",
    "idempotency_conflict",
    "state_conflict",
    "unsupported_encrypted_template",
    "package_corrupt",
    "font_missing",
    "render_failed",
    "provider_unavailable",
    "provider_capability_missing",
    "structured_output_invalid",
    "composition_failed",
    "protected_slide",
    "validation_failed",
    "time_budget_exceeded",
    "artifact_missing",
    "internal_error",
}


@dataclass
class DeckDNAError(Exception):
    """Typed failure crossing layer boundaries. Never leaks secrets."""

    code: str
    message: str
    stage: str | None = None
    retryable: bool = False
    http_status: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {self.code}")
        super().__init__(self.message)

    def to_envelope(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "stage": self.stage,
                "retryable": self.retryable,
                "request_id": request_id,
                "details": self.details,
            }
        }
