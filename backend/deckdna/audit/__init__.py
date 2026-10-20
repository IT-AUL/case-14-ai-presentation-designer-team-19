"""Render Arena — аудит готовых .pptx (docs/pipeline/AUDIT_AND_REPAIR.md)."""

from deckdna.audit.basic import (
    RULE_ASPECT_RATIO,
    RULE_EMPTY_SLIDE,
    RULE_RASTER_ONLY,
    RULE_TEXT_OVERFLOW,
    RULES,
    audit_deck,
    check_aspect_ratio,
    check_empty_slide,
    check_raster_only,
    check_text_overflow,
)
from deckdna.audit.config import (
    AuditConfig,
    default_audit_config,
    load_audit_config,
)
from deckdna.audit.issues import SCHEMA_VERSION, AuditIssue

__all__ = [
    "AuditConfig",
    "default_audit_config",
    "load_audit_config",
    "RULE_ASPECT_RATIO",
    "RULE_EMPTY_SLIDE",
    "RULE_RASTER_ONLY",
    "RULE_TEXT_OVERFLOW",
    "RULES",
    "SCHEMA_VERSION",
    "AuditIssue",
    "audit_deck",
    "check_aspect_ratio",
    "check_empty_slide",
    "check_raster_only",
    "check_text_overflow",
]
