"""Author constraints independent of actual plan length (ADR-047)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from deckdna.contracts.deck_plan import Brief, SlideCountPolicy
from deckdna.errors import DeckDNAError


def validate_count_constraint(brief: Brief, actual: int, *, stage: str = "planning") -> None:
    try:
        policy = SlideCountPolicy(brief.slide_count_policy or SlideCountPolicy.preferred)
    except ValueError as exc:
        raise DeckDNAError("invalid_input", "unknown slide count policy", stage=stage,
                           http_status=422) from exc
    limit = brief.requested_slide_count or brief.target_slide_count
    violated = ((policy == SlideCountPolicy.exact and actual != limit)
                or (policy == SlideCountPolicy.maximum and actual > limit))
    if violated:
        raise DeckDNAError(
            "validation_failed", f"slide count conflicts with {policy.value} requirement",
            stage=stage, http_status=422,
            details={"rule": "request.slide_count", "policy": policy.value,
                     "required": limit, "actual": actual,
                     "choices": ["change_count", "change_content", "change_fill_policy"]},
        )


def resolve_count_goal(brief: Brief) -> Brief:
    limit = brief.requested_slide_count or brief.target_slide_count
    if brief.slide_count_policy == SlideCountPolicy.exact:
        validate_count_constraint(brief, brief.target_slide_count)
        return brief
    if brief.slide_count_policy == SlideCountPolicy.maximum:
        return brief.model_copy(update={"target_slide_count": min(brief.target_slide_count, limit)})
    return brief


def validate_deck_count(
    brief: Brief, deck: bytes | Path, *, stage: str = "composing",
) -> int | None:
    if brief.slide_count_policy not in (SlideCountPolicy.exact, SlideCountPolicy.maximum):
        return
    try:
        with zipfile.ZipFile(io.BytesIO(deck) if isinstance(deck, bytes) else deck) as package:
            root = ElementTree.fromstring(package.read("ppt/presentation.xml"))  # noqa: S314
        ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
        count = len(root.findall(f"{{{ns}}}sldIdLst/{{{ns}}}sldId"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise DeckDNAError("package_corrupt", "cannot verify compiled slide count",
                           stage=stage) from exc
    validate_count_constraint(brief, count, stage=stage)
    return count


def validate_pdf_count(brief: Brief, pdf: Path, expected: int | None) -> None:
    if brief.slide_count_policy not in (SlideCountPolicy.exact, SlideCountPolicy.maximum):
        return
    import pymupdf

    try:
        with pymupdf.open(pdf) as document:
            actual = len(document)
    except (RuntimeError, ValueError, OSError) as exc:
        raise DeckDNAError("render_failed", "cannot verify exported PDF page count",
                           stage="exporting.render") from exc
    validate_count_constraint(brief, actual, stage="exporting.render")
    if expected is not None and actual != expected:
        raise DeckDNAError("validation_failed", "PDF page count differs from the compiled deck",
                           stage="exporting.render", http_status=422,
                           details={"rule": "request.export_count", "required": expected,
                                    "actual": actual})
