"""AuditIssue contract (schemas/audit-issue.schema.json).

Deterministic rules emit ``deterministic=True`` issues; VLM/contextual
checks (not implemented here) will set it False. ``to_dict`` output must
validate against the frozen JSON schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"
RULE_VERSION = "audit-basic/0.1.0"

Severity = str  # "info" | "warning" | "error" | "blocker"


@dataclass
class AuditIssue:
    rule_code: str
    severity: Severity
    message: str
    audit_run_id: str
    id: str
    schema_version: str = SCHEMA_VERSION
    deterministic: bool = True
    deck_revision: int = 0
    slide_id: str | None = None
    slide_index: int | None = None
    shape_ids: list[str] = field(default_factory=list)
    bbox: dict[str, float] | None = None
    measured_value: str | float | None = None
    threshold: str | float | None = None
    evidence: list[dict[str, str]] = field(default_factory=list)
    confidence: float | None = 1.0
    status: str = "open"
    repairable: bool = True
    proposed_actions: list[str] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=lambda: {"rule_version": RULE_VERSION})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "audit_run_id": self.audit_run_id,
            "deck_revision": self.deck_revision,
            "rule_code": self.rule_code,
            "deterministic": self.deterministic,
            "severity": self.severity,
            "slide_id": self.slide_id,
            "slide_index": self.slide_index,
            "shape_ids": self.shape_ids,
            "bbox": self.bbox,
            "message": self.message,
            "measured_value": self.measured_value,
            "threshold": self.threshold,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "status": self.status,
            "repairable": self.repairable,
            "proposed_actions": self.proposed_actions,
            "provenance": self.provenance,
        }
