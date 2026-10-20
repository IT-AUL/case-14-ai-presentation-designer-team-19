"""Verbatim text guards; presence is separate from readability and support."""
from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind
from deckdna.errors import DeckDNAError
from deckdna.pptx.composing.slide_swap import slide_parts

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
REPLACED_BODY_KINDS = frozenset({Kind.paragraph, Kind.bullet, Kind.number, Kind.quote, Kind.label})


def _conflict(slide: str, reason: str, *, stage: str = "planning") -> None:
    raise DeckDNAError(
        "validation_failed", "locked author copy cannot be changed or discarded",
        stage=stage, http_status=422,
        details={"rule": "request.locked_copy", "slide_id": slide, "reason": reason,
                 "choices": ["change_layout", "change_content", "explicit_replacement"]},
    )


def replacement_kinds(action: str) -> frozenset[Kind]:
    return (frozenset({Kind.title}) if action == "replace_title" else
            REPLACED_BODY_KINDS if action == "replace_text" else frozenset())


def _signature(unit: ContentUnit) -> tuple:
    return (unit.kind, unit.role, unit.text, unit.asset_ref, unit.table_ref,
            unit.chart_ref, unit.diagram_ref)


def validate_locked_transition(
    before: DeckPlan, after: DeckPlan, *, allow_moves: bool = False,
    replacement_slide: str | None = None, replaced_kinds: frozenset[Kind] = frozenset(),
) -> None:
    def signatures(plan: DeckPlan, *, old: bool) -> Counter:
        return Counter(
            (None if allow_moves else slide.id, _signature(unit))
            for slide in plan.slides for unit in slide.content_units
            if unit.locked and not (old and slide.id == replacement_slide
                                    and unit.kind in replaced_kinds)
        )

    missing = signatures(before, old=True) - signatures(after, old=False)
    if missing:
        slide_id = next(iter(missing))[0] or "relocated"
        _conflict(slide_id, "plan_changed_locked_unit")


def locked_slide_indices(plan: DeckPlan, reports: list[dict] | None = None) -> set[int]:
    locked = {s.id for s in plan.slides if any(u.locked for u in s.content_units)}
    if reports is not None:
        return {i for i, report in enumerate(reports) if report.get("id") in locked}
    return {i for i, slide in enumerate(plan.slides) if slide.id in locked}


def _normal(text: str) -> str:
    return " ".join(text.split())


def validate_locked_deck(
    plan: DeckPlan, deck: bytes | Path, reports: list[dict] | None = None,
) -> None:
    wanted = {s.id: [u for u in s.content_units if u.locked] for s in plan.slides}
    wanted = {sid: units for sid, units in wanted.items() if units}
    if not wanted:
        return
    try:
        with zipfile.ZipFile(io.BytesIO(deck) if isinstance(deck, bytes) else deck) as archive:
            parts = {name: archive.read(name) for name in (
                "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels",
            )}
            ordered = slide_parts(parts)
            # Do not duplicate embedded media/workbooks in memory for a text check.
            parts.update({part: archive.read(part) for part in ordered})
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        raise DeckDNAError("package_corrupt", "cannot verify locked author copy",
                           stage="composing") from exc
    if reports is None and len(ordered) == len(plan.slides):
        reports = [{"id": s.id} for s in plan.slides]
    if reports is None or len(reports) != len(ordered):
        _conflict(next(iter(wanted)), "physical_slide_mapping_unavailable", stage="composing")
    texts: dict[str, list[str]] = {}
    for part, report in zip(ordered, reports, strict=True):
        sid = report.get("id")
        if sid not in wanted:
            continue
        try:
            root = etree.fromstring(parts[part])
        except etree.XMLSyntaxError as exc:
            raise DeckDNAError("package_corrupt", "cannot verify locked author copy",
                               stage="composing") from exc
        for body in root.iter(f"{_P}txBody"):
            if any(e.get("hidden") in {"1", "true"} for ancestor in body.iterancestors()
                   if ancestor.tag in {f"{_P}sp", f"{_P}grpSp"}
                   for e in (ancestor.findall(f"{_P}nvSpPr/{_P}cNvPr")
                             + ancestor.findall(f"{_P}nvGrpSpPr/{_P}cNvPr"))):
                continue
            # Adjacent runs concatenate, paragraph/line breaks become spaces.
            paragraphs = ["".join(" " if node.tag == f"{_A}br" else node.text or ""
                                   for node in p.iter() if node.tag in {f"{_A}t", f"{_A}br"})
                          for p in body.findall(f"{_A}p")]
            texts.setdefault(sid, []).append(_normal(" ".join(paragraphs)))
    for sid, units in wanted.items():
        expected = Counter(_normal(u.text or "") for u in units)
        if "" in expected or any(u.kind in {Kind.table, Kind.chart, Kind.diagram, Kind.image}
                                 for u in units):
            _conflict(sid, "structured_or_empty_lock_unverifiable", stage="composing")
        for text, count in expected.items():
            pattern = ((r"(?<!\w)" if text[0].isalnum() else "") + re.escape(text)
                       + (r"(?!\w)" if text[-1].isalnum() else ""))
            if sum(len(re.findall(pattern, t)) for t in texts.get(sid, [])) < count:
                _conflict(sid, "native_text_missing_or_changed", stage="composing")
