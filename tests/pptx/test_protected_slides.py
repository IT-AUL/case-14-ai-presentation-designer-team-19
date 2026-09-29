"""Protected slides (e.g. mandatory submission slides 7–11) must be
untouchable by generation, exemplar swap and repair."""

import shutil
from pathlib import Path

import pytest
from deckdna.audit.basic import audit_deck
from deckdna.errors import DeckDNAError
from deckdna.generation.pipeline import generate
from deckdna.pptx.composing.protection import ProtectedSlides
from deckdna.pptx.opc.package import OpcPackage
from deckdna.repair.apply import apply_repairs
from deckdna.repair.planner import plan_repairs
from pptx import Presentation

PROTECTED = ProtectedSlides(range(6, 11))  # 0-based indices of slides 7-11
SLIDE_INDEX = {"s7": 6, "s8": 7, "s12": 11, "s99": 30}


def _action(action_type: str, slide_id: str) -> dict:
    return {
        "schema_version": "1",
        "action_type": action_type,
        "target": {"slide_id": slide_id},
        "params": {},
    }


def test_repair_on_protected_slide_rejected():
    with pytest.raises(DeckDNAError) as exc:
        PROTECTED.guard_action(_action("shorten_text", "s7"), SLIDE_INDEX)
    assert exc.value.code == "protected_slide"


def test_every_mutation_kind_rejected_on_protected():
    for kind in (
        "rewrite_title",
        "swap_exemplar",
        "move_shape",
        "map_font",
        "remove_group",
        "native_rebuild",
    ):
        with pytest.raises(DeckDNAError):
            PROTECTED.guard_action(_action(kind, "s8"), SLIDE_INDEX)


def test_repair_on_unprotected_slide_allowed():
    PROTECTED.guard_action(_action("shorten_text", "s12"), SLIDE_INDEX)


def test_reorder_rejected_while_protected():
    with pytest.raises(DeckDNAError) as exc:
        PROTECTED.guard_action(_action("reorder_slides", "s12"), SLIDE_INDEX)
    assert exc.value.code == "protected_slide"
    # No protected slides configured -> reorder fine.
    ProtectedSlides().guard_action(_action("reorder_slides", "s12"), SLIDE_INDEX)


def test_filter_actions_splits_without_raising():
    actions = [
        _action("shorten_text", "s7"),   # protected -> rejected
        _action("shorten_text", "s12"),  # allowed
        _action("map_color", "s8"),      # protected -> rejected
    ]
    allowed, rejected = PROTECTED.filter_actions(actions, SLIDE_INDEX)
    assert len(allowed) == 1 and len(rejected) == 2


def test_check_direct_for_composition_path():
    with pytest.raises(DeckDNAError):
        PROTECTED.check(9, "compose.exemplar_swap")
    PROTECTED.check(12, "compose.exemplar_swap")


def test_config_carries_protected_indices():
    from deckdna.planning.config import GenerationConfig

    cfg = GenerationConfig(protected_slide_indices=[6, 7, 8, 9, 10])
    guard = ProtectedSlides(cfg.protected_slide_indices)
    assert guard.is_protected(8) and not guard.is_protected(11)


# --- e2e: реальный generate() + apply_repairs с защитой --------------

SUBMISSION = Path("tests/fixtures/pptx/lct2026_submission.pptx")
ARTICLE = Path("tests/fixtures/content/poc_article.md")
PROT_IDX = [6, 7, 8, 9, 10]  # обязательные submission-слайды 7–11 (0-based)
HAS_SOFFICE = shutil.which("soffice") is not None


def _template_part_order(template: Path) -> list[str]:
    prs = Presentation(str(template))
    return [str(s.part.partname).lstrip("/") for s in prs.slides]


def test_generate_carries_protected_verbatim(tmp_path):
    """generate() с protected_slide_indices: обязательные слайды идут
    в выходную колоду байт-в-байт на своих позициях (OR-031),
    контентные слайды раздвигаются."""
    if not HAS_SOFFICE:
        pytest.skip("soffice not installed — pdf stage needs it")
    brief = {
        "purpose": "защита submission-слайдов",
        "audience": "жюри",
        "language": "ru",
        "target_slide_count": 10,
    }
    res = generate(
        str(SUBMISSION), str(ARTICLE), brief, tmp_path / "out",
        protected_slide_indices=PROT_IDX,
    )
    assert res["slides_out"] == 10 + len(PROT_IDX)
    assert res["compose_report"]["protected_indices"] == PROT_IDX

    tpl = OpcPackage.open(str(SUBMISSION))
    out = OpcPackage.open(res["artifacts"]["pptx"])
    tpl_order = _template_part_order(SUBMISSION)
    for i in PROT_IDX:
        out_part = f"ppt/slides/slide{i + 1}.xml"
        assert out.parts[out_part] == tpl.parts[tpl_order[i]], (
            f"protected slide {i} must be byte-identical"
        )
    # контентный слайд сразу за protected-зоной — подменён, не вербатим
    assert out.parts["ppt/slides/slide12.xml"] != tpl.parts[tpl_order[11]]


def test_repair_skips_protected_slides(tmp_path):
    """apply_repairs с protected: действия на защищённые слайды —
    skipped, сами слайды остаются нетронутыми."""
    if not HAS_SOFFICE:
        pytest.skip("soffice not installed — pdf stage needs it")
    brief = {
        "purpose": "защита submission-слайдов",
        "audience": "жюри",
        "language": "ru",
        "target_slide_count": 10,
    }
    res = generate(
        str(SUBMISSION), str(ARTICLE), brief, tmp_path / "out",
        protected_slide_indices=PROT_IDX,
    )
    deck = res["artifacts"]["pptx"]
    before = Presentation(deck)

    report = apply_repairs(
        deck, plan_repairs(audit_deck(deck)),
        tmp_path / "fixed.pptx", protected=PROT_IDX,
    )
    prot_skipped = [
        r for r in report.results if "protected_slide" in (r.detail or "")
    ]
    assert prot_skipped, "ожидались действия, отклонённые гардом"

    fixed = Presentation(str(tmp_path / "fixed.pptx"))
    for i in PROT_IDX:
        assert fixed.slides[i].element.xml == before.slides[i].element.xml


def test_apply_has_no_side_effects_on_protected_slides(tmp_path):
    """Без soffice: protected-слайды бит-в-бит нетронуты после apply.

    Регрессия: shorten_text строил audit._Ctx — его duplicate-скан
    (_slide_text_tokens) обращался к shape.text_frame на ВСЕХ слайдах,
    и python-pptx get_or_add_txBody() создавал пустые txBody у pic-
    плейсхолдеров protected-слайдов — XML менялся без единого applied.
    """
    brief = {
        "purpose": "защита submission-слайдов",
        "audience": "жюри",
        "language": "ru",
        "target_slide_count": 10,
    }
    res = generate(
        str(SUBMISSION), str(ARTICLE), brief, tmp_path / "out",
        protected_slide_indices=PROT_IDX,
    )
    deck = res["artifacts"]["pptx"]
    before = Presentation(deck)

    apply_repairs(
        deck, plan_repairs(audit_deck(deck)),
        tmp_path / "fixed.pptx", protected=PROT_IDX,
    )

    fixed = Presentation(str(tmp_path / "fixed.pptx"))
    for i in PROT_IDX:
        assert fixed.slides[i].element.xml == before.slides[i].element.xml
