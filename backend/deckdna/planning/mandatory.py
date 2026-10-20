"""Conservative source anchors for required sections; not full semantic coverage."""
from __future__ import annotations

from deckdna.contracts.content_pack import ContentPack, Section
from deckdna.contracts.deck_plan import Brief, ContentUnit, DeckPlan, Kind, Purpose
from deckdna.errors import DeckDNAError

_BODY = frozenset({Kind.paragraph, Kind.bullet, Kind.quote})
_NAVIGATION = frozenset({Purpose.title, Purpose.agenda, Purpose.section_divider})


def _normal(text: str) -> str:
    return " ".join(text.casefold().split())


def _conflict(entry: str, reason: str, choices: list[str]) -> None:
    raise DeckDNAError(
        "validation_failed", "required section needs substantive source content",
        stage="planning", http_status=422,
        details={"rule": "request.mandatory_section", "section": entry, "reason": reason,
                 "choices": choices},
    )


def mandatory_anchors(
    pack: ContentPack, entries: list[str] | None,
) -> list[tuple[Section, ContentUnit]]:
    if not entries:
        return []
    from deckdna.planning.story_director import _block_to_units

    anchors = []
    seen = set()
    for entry in entries:
        key = _normal(entry)
        exact = [s for s in pack.sections if _normal(s.id) == key] if key else []
        matches = exact or [s for s in pack.sections if key and _normal(s.heading) == key]
        if len(matches) != 1:
            _conflict(entry, "unknown_section" if not matches else "ambiguous_heading",
                      [s.id for s in matches or pack.sections])
        section = matches[0]
        if section.id in seen:
            continue
        seen.add(section.id)
        units = [u for u in _block_to_units(pack.id, section)
                 if u.kind in _BODY and u.text and u.text.strip()]
        if not units:
            _conflict(entry, "substantive_text_unavailable",
                      ["add_source_content", "change_requirement"])
        anchor = max(units, key=lambda u: len(u.text or ""))
        anchors.append((section, anchor))
    return anchors


def _matches(unit: ContentUnit, anchor: ContentUnit) -> bool:
    return (unit.kind in _BODY and unit.text == anchor.text
            and bool(set(unit.evidence_ids or ()) & set(anchor.evidence_ids or ())))


def protect_mandatory_groups(groups: list, pack: ContentPack, brief: Brief) -> list:
    anchors = mandatory_anchors(pack, brief.mandatory_sections)
    if not anchors:
        return groups
    return [(section, [u.model_copy(update={"locked": True})
                       if any(_matches(u, a) for _, a in anchors) else u for u in units])
            for section, units in groups]


def protect_mandatory_sections(
    plan: DeckPlan, pack: ContentPack, *, required: list[str] | None = None,
) -> DeckPlan:
    anchors = mandatory_anchors(pack, [*(plan.brief.mandatory_sections or []), *(required or [])])
    if not anchors:
        return plan
    slides = list(plan.slides)
    for section, anchor in anchors:
        found = next(((si, ui) for si, s in enumerate(slides) if s.purpose not in _NAVIGATION
                      for ui, u in enumerate(s.content_units) if _matches(u, anchor)), None)
        if found is None:
            _conflict(section.id, "source_anchor_missing_from_plan",
                      ["restore_source_content", "change_requirement"])
        si, ui = found
        units = list(slides[si].content_units)
        units[ui] = units[ui].model_copy(update={"locked": True})
        slides[si] = slides[si].model_copy(update={"content_units": units, "mandatory": True})
    return plan.model_copy(update={"slides": slides})
