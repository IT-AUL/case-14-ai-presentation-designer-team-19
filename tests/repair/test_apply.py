"""apply_repairs: реальная мутация .pptx по плану из planner.py."""

import json
from collections import Counter
from pathlib import Path

import pytest
from conftest import inject_text_overflow
from deckdna.audit.basic import (
    RULE_ANCHOR_POSITION,
    RULE_OUT_OF_BOUNDS,
    RULE_PLACEHOLDER_TEXT,
    RULE_SLIDE_CLIP,
    RULE_TEXT_OVERFLOW,
    RULE_UNINTENDED_OVERLAP,
    audit_deck,
)
from deckdna.contracts.deck_plan import Brief
from deckdna.contracts.repair_action import (
    ActionType,
    Bbox,
    Params,
    RepairAction,
    Target,
)
from deckdna.generation.pipeline import generate
from deckdna.repair.apply import apply_repairs
from deckdna.repair.planner import plan_repairs, plan_repairs_with_report
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Inches, Pt

FIXTURE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
CONTENT = Path("tests/fixtures/content/poc_article.md")

BRIEF = Brief(
    purpose="Проверка repair loop",
    audience="эксперты",
    language="ru",
    target_slide_count=12,
)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    if not FIXTURE.exists() or not CONTENT.exists():
        pytest.skip("organizer fixture not present")
    out_dir = tmp_path_factory.mktemp("repair_gen")
    return generate(str(FIXTURE), str(CONTENT), BRIEF, out_dir)


def _action(atype, slide_id, shape_ids, **params) -> RepairAction:
    return RepairAction(
        schema_version="1.0",
        action_type=atype,
        issue_ids=["test:issue"],
        target=Target(slide_id=str(slide_id), shape_ids=shape_ids),
        params=Params(**params),
    )


def test_apply_reduces_real_issues(generated, tmp_path):
    """E2E на реальном шаблоне: issues после apply строго меньше.

    Чистая генерация на vk_tech не обязана давать repairable issues —
    auto-fix закрывает aspect/contrast/palette до ревизии 1, а текст
    текущим пайплайном умещается. Поэтому в копию колоды детерминированно
    вносится настоящий text.overflow (conftest.inject_text_overflow) —
    реальный дефект в байтах, не синтетический AuditIssue."""
    corrupted = tmp_path / "corrupted.pptx"
    corrupted.write_bytes(
        inject_text_overflow(
            Path(generated["artifacts"]["pptx"]).read_bytes()
        )
    )
    before = audit_deck(corrupted)
    plan = plan_repairs_with_report(before)
    assert plan.actions, "ожидались repairable issues на vk_tech_template"

    out = tmp_path / "fixed.pptx"
    report = apply_repairs(corrupted, plan.actions, out)
    assert out.exists()
    assert report.failed == 0
    # resize_shape реализован — всё что planner выдал этого типа, должно
    # примениться. recrop_image здесь не ожидается: с Q2 конвейер сам
    # чинит image.aspect_ratio до ревизии 1 (recrop покрыт отдельно —
    # test_recrop_image_fixes_aspect)
    assert report.applied >= 1
    assert report.by_type.get("resize_shape", {}).get("applied", 0) >= 1
    assert Counter(i.rule_code for i in before)["image.aspect_ratio"] == 0

    after = audit_deck(out)
    assert len(after) < len(before)
    # text.overflow строго уменьшён — residual легитимен, если геометрия
    # и shorten_text исчерпаны (текст длиннее рамки в пределах слайда)
    n_ov = Counter(i.rule_code for i in before)[RULE_TEXT_OVERFLOW]
    n_ov_after = Counter(i.rule_code for i in after)[RULE_TEXT_OVERFLOW]
    assert n_ov_after < n_ov
    assert not [i for i in after if i.rule_code == "image.aspect_ratio"]
    # repair не создаёт новых коллизий с соседями
    n_pair = Counter(i.rule_code for i in before)[RULE_UNINTENDED_OVERLAP]
    n_pair_after = Counter(i.rule_code for i in after)[RULE_UNINTENDED_OVERLAP]
    assert n_pair_after <= n_pair


def test_resize_shape_writes_xfrm(generated, tmp_path):
    """resize_shape реально меняет off/ext в нормализованной системе bbox."""
    pptx = generated["artifacts"]["pptx"]
    prs = Presentation(str(pptx))
    slide = prs.slides[0]
    shape = next(s for s in slide.shapes if s.has_text_frame and s.width)

    new_bbox = Bbox(x=0.05, y=0.05, w=0.5, h=0.3)
    act = _action(
        ActionType.resize_shape,
        slide.slide_id,
        [str(shape.shape_id)],
        bbox=new_bbox,
    )
    out = tmp_path / "resized.pptx"
    report = apply_repairs(pptx, [act], out)
    assert report.applied == 1

    prs2 = Presentation(str(out))
    sh2 = prs2.slides[0].shapes
    target = next(s for s in sh2 if str(s.shape_id) == str(shape.shape_id))
    sw, sh = prs2.slide_width, prs2.slide_height
    assert abs(target.left - new_bbox.x * sw) < 2
    assert abs(target.top - new_bbox.y * sh) < 2
    assert abs(target.width - new_bbox.w * sw) < 2
    assert abs(target.height - new_bbox.h * sh) < 2


def test_recrop_image_fixes_aspect(generated, tmp_path):
    """recrop_image: картинка с искажённым aspect → кроп до aspect кадра."""
    pptx = generated["artifacts"]["pptx"]
    issues = audit_deck(pptx)
    aspect = [i for i in issues if i.rule_code == "image.aspect_ratio"]
    if not aspect:
        pytest.skip("в сгенерированной колоде нет image.aspect_ratio")
    issue = aspect[0]
    act = _action(
        ActionType.recrop_image,
        issue.slide_id,
        issue.shape_ids,
        keep_aspect=True,
    )
    out = tmp_path / "recropped.pptx"
    report = apply_repairs(pptx, [act], out)
    assert report.applied == 1
    assert report.results[0].shapes == issue.shape_ids

    after = audit_deck(out)
    # одинаковый shape_id встречается на разных слайдах — матчим и по slide
    assert not [
        i
        for i in after
        if i.rule_code == "image.aspect_ratio"
        and i.shape_ids == issue.shape_ids
        and i.slide_id == issue.slide_id
    ]


def test_merge_slide_removes_duplicate(tmp_path):
    """merge_slide: слайд уходит из sldIdLst + rels, пакет валиден,
    duplicate_slide issues обнуляются. На vk_tech_template детерминиро-
    ванно 8 дублей (см. golden)."""
    from deckdna.pptx.opc.package import OpcPackage

    if not FIXTURE.exists():
        pytest.skip("organizer fixture not present")
    before = audit_deck(FIXTURE)
    dups = [i for i in before if i.rule_code == "integrity.duplicate_slide"]
    assert len(dups) == 8  # golden-число — ловит регресс детектора
    plan = plan_repairs_with_report(before)
    merges = [a for a in plan.actions if a.action_type == ActionType.merge_slide]
    assert len(merges) == len(dups)

    n_slides = len(Presentation(str(FIXTURE)).slides._sldIdLst)
    out = tmp_path / "merged.pptx"
    report = apply_repairs(FIXTURE, merges, out)
    assert report.by_type["merge_slide"]["applied"] == len(merges)
    assert report.failed == 0

    prs2 = Presentation(str(out))
    assert len(prs2.slides._sldIdLst) == n_slides - len(merges)
    assert OpcPackage.open(out).check_rel_integrity() == []
    after = audit_deck(out)
    assert not [i for i in after if i.rule_code == "integrity.duplicate_slide"]


def test_merge_slide_missing_target_skipped(tmp_path):
    """merge_slide на несуществующий slide_id → skipped, файл цел."""
    if not FIXTURE.exists():
        pytest.skip("organizer fixture not present")
    n_slides = len(Presentation(str(FIXTURE)).slides._sldIdLst)
    act = _action(ActionType.merge_slide, "999999", None, new_index=1)
    out = tmp_path / "merge_missing.pptx"
    report = apply_repairs(FIXTURE, [act], out)
    assert report.results[0].status == "skipped"
    assert len(Presentation(str(out)).slides._sldIdLst) == n_slides


def test_unimplemented_and_skipped(generated, tmp_path):
    """split_slide/native_rebuild → not_implemented; битый target → skipped."""
    pptx = generated["artifacts"]["pptx"]
    prs = Presentation(str(pptx))
    slide_id = str(prs.slides[0].slide_id)

    acts = [
        _action(ActionType.split_slide, slide_id, None, new_index=1),
        _action(ActionType.native_rebuild, slide_id, None),
        _action(ActionType.resize_shape, "999999", ["1"], bbox=Bbox(w=0.4)),
        _action(ActionType.resize_shape, slide_id, ["no-such"], bbox=Bbox(w=0.4)),
        _action(ActionType.resize_shape, slide_id, ["1"], bbox=None),
    ]
    out = tmp_path / "mixed.pptx"
    report = apply_repairs(pptx, acts, out)
    assert out.exists()
    statuses = Counter(r.status for r in report.results)
    assert statuses["not_implemented"] == 2
    assert statuses["skipped"] == 2  # нет slide_id 999999 + bbox=None
    assert statuses["failed"] == 1  # shape 'no-such' не найден
    d = report.to_dict()
    assert d["applied"] + d["skipped"] + d["failed"] + d["not_implemented"] == 5


def test_apply_missing_input(tmp_path):
    from deckdna.errors import DeckDNAError

    with pytest.raises(DeckDNAError) as exc:
        apply_repairs(tmp_path / "none.pptx", [], tmp_path / "o.pptx")
    assert exc.value.stage == "repair.apply"


def test_shorten_text_skips_when_resize_already_fit(tmp_path):
    """shorten_text — самопроверяющийся fallback: текст, поместившийся
    после resize, не режется (skipped), overflow не возвращается."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(2), Inches(0.4)
    )
    tf = box.text_frame
    tf.word_wrap = True
    tf.text = "очень длинный текст который раньше не помещался в рамку"
    src = tmp_path / "ov.pptx"
    prs.save(src)

    slide_id = str(slide.slide_id)
    shape_id = str(box.shape_id)
    # resize уже увеличил рамку до вмещающей высоты — shorten обязан
    # честно пропустить (текст ~157.5pt оценочной высоты < ~201pt usable)
    prs2 = Presentation(str(src))
    sh = prs2.slides[0].shapes[0]
    sh.height = Inches(3)
    src2 = tmp_path / "grown.pptx"
    prs2.save(src2)

    action = _action(ActionType.shorten_text, slide_id, [shape_id])
    out = tmp_path / "fixed.pptx"
    report = apply_repairs(src2, [action], out)
    assert report.skipped == 1 and report.failed == 0
    fixed = Presentation(str(out))
    assert fixed.slides[0].shapes[0].text_frame.text == tf.text


def test_shorten_text_truncates_when_resize_capped(tmp_path):
    """shorten_text при зажатой геометрии: текст урезается словами с
    '…', повторный аудит без text.overflow, границы слайда не нарушены."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    sw, sh_emu = prs.slide_width, prs.slide_height
    box = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        int(sw * 0.936),
        int(sh_emu * 0.927),
        int(sw * 0.046),
        int(sh_emu * 0.053),
    )
    tf = box.text_frame
    tf.word_wrap = True
    tf.text = (
        "оченьдлинноенеразбиваемоесловоинтеграция ещё много текста "
        "для переполнения по высоте рамки"
    )
    tf.paragraphs[0].runs[0].font.size = Pt(14)
    src = tmp_path / "corner.pptx"
    prs.save(src)

    before = audit_deck(src)
    ov = [i for i in before if i.rule_code == RULE_TEXT_OVERFLOW]
    assert len(ov) == 1

    actions = [
        a for a in plan_repairs(before) if a.issue_ids == [ov[0].id]
    ]
    assert [a.action_type for a in actions] == [
        ActionType.resize_shape,
        ActionType.shorten_text,
    ]
    out = tmp_path / "fixed.pptx"
    report = apply_repairs(src, actions, out)
    assert report.failed == 0
    shorten = next(r for r in report.results if r.action_type == "shorten_text")
    assert shorten.status == "applied"

    fixed = Presentation(str(out))
    text = fixed.slides[0].shapes[0].text_frame.text
    assert len(text) < len(tf.text) and "…" in text
    after = audit_deck(out)
    assert not [i for i in after if i.rule_code == RULE_TEXT_OVERFLOW]
    assert not [i for i in after if i.rule_code == RULE_OUT_OF_BOUNDS]


def test_report_json_serializable(generated, tmp_path):
    out = tmp_path / "r.pptx"
    report = apply_repairs(generated["artifacts"]["pptx"], [], out)
    json.dumps(report.to_dict())
    assert report.applied == 0


def test_remove_placeholder_clears_runs(tmp_path):
    """remove_placeholder: стоковый 'Lorem ipsum' уходит, фигура
    остаётся в дереве; повторный аудит — 0 placeholder issues."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(4), Inches(1)
    )
    box.text_frame.text = "Lorem ipsum dolor sit amet"
    src = tmp_path / "lorem.pptx"
    prs.save(src)

    before = audit_deck(src)
    ph = [i for i in before if i.rule_code == RULE_PLACEHOLDER_TEXT]
    assert len(ph) == 1

    actions = plan_repairs(before)
    ph_actions = [a for a in actions if a.action_type == ActionType.remove_placeholder]
    assert len(ph_actions) == 1

    out = tmp_path / "fixed.pptx"
    report = apply_repairs(src, ph_actions, out)
    assert report.applied == 1 and report.failed == 0

    fixed = Presentation(str(out))
    shape = next(s for s in fixed.slides[0].shapes if str(s.shape_id) == "2")
    assert shape.text_frame.text == ""  # фигура на месте, текст очищен
    ph_after = [
        i for i in audit_deck(out) if i.rule_code == RULE_PLACEHOLDER_TEXT
    ]
    assert ph_after == []


def test_resize_shape_clamps_to_slide_bounds(tmp_path):
    """Bbox за краем слайда зажимается в [0,1], не создавая OOB.

    Planner зажимает расширение при построении плана, но apply не
    доверяет bbox слепо: любой источник action (или edge-case планера)
    не должен породить layout.out_of_bounds взамен text.overflow.
    Остаточный overflow при этом законно остаётся issue.
    """
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(
        Inches(8.5), Inches(5), Inches(1.3), Inches(2)
    )
    box.text_frame.text = "текст"
    src = tmp_path / "edge.pptx"
    prs.save(src)

    # adversarial bbox: right edge 1.3, bottom edge 1.7 — за границами
    action = RepairAction(
        schema_version="1.0",
        action_type=ActionType.resize_shape,
        issue_ids=["t1"],
        target=Target(
            slide_id=str(slide.slide_id), shape_ids=[str(box.shape_id)]
        ),
        params=Params(bbox=Bbox(x=0.85, y=0.5, w=0.45, h=1.2)),
    )
    out = tmp_path / "fixed.pptx"
    report = apply_repairs(src, [action], out)
    assert report.applied == 1
    assert "зажат" in report.results[0].detail

    fixed = Presentation(str(out))
    sh = fixed.slides[0].shapes[0]
    sw, shh = float(fixed.slide_width), float(fixed.slide_height)
    assert sh.left >= 0 and sh.top >= 0
    assert sh.left + sh.width <= sw
    assert sh.top + sh.height <= shh
    oob = [
        i for i in audit_deck(out) if i.rule_code == RULE_OUT_OF_BOUNDS
    ]
    assert oob == []


def test_resize_shape_degenerate_clamp_skips_not_corrupts(tmp_path):
    """Live репро on a generated deck: a shape whose own top already
    sits past the slide's bottom edge (bbox.y > 1.0 -- the clamp-into-
    [0,sh] logic that protects against out_of_bounds forces height to 0
    for ANY such shape, not just ones with a negative requested h).
    Before the fix this zeroed the shape's real geometry and reported
    "applied"; the chained shorten_text then failed outright on the
    corrupted 0-height box ("нет геометрии для оценки вместимости"),
    even though the shape had perfectly real, measurable geometry
    before this resize ran. Now: honest skip, original geometry
    untouched, so shorten_text (or anything else downstream) still has
    something real to work with."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    sh_emu = prs.slide_height
    # top already past the bottom edge -- mirrors the live repro
    # (bbox y=1.087 measured by the audit on the shape's actual, already
    # out-of-bounds position) rather than a resize target asking to grow
    # past the edge from a normal starting position.
    box = slide.shapes.add_textbox(
        Inches(1), int(sh_emu * 1.05), Inches(2), Inches(0.5)
    )
    box.text_frame.text = "уже за нижним краем слайда"
    orig_left, orig_top = box.left, box.top
    orig_width, orig_height = box.width, box.height
    src = tmp_path / "already_oob.pptx"
    prs.save(src)

    action = RepairAction(
        schema_version="1.0",
        action_type=ActionType.resize_shape,
        issue_ids=["t1"],
        target=Target(
            slide_id=str(slide.slide_id), shape_ids=[str(box.shape_id)]
        ),
        # y unchanged (matches the shape's own already-OOB position, as
        # the real planner does for text.overflow -- it never rewrites
        # x/y, only w/h), grow height -- this is exactly what collapses
        # to a negative/zero clamp once top pins to the slide edge.
        params=Params(bbox=Bbox(x=1 / 12, y=1.05, w=0.2, h=0.3)),
    )
    out = tmp_path / "fixed.pptx"
    report = apply_repairs(src, [action], out)

    assert report.failed == 0
    assert report.results[0].status == "skipped"
    assert "нулевые width/height" in report.results[0].detail

    fixed = Presentation(str(out))
    sh = fixed.slides[0].shapes[0]
    # geometry left exactly as it was -- not corrupted into a 0-size box
    assert (sh.left, sh.top, sh.width, sh.height) == (
        orig_left,
        orig_top,
        orig_width,
        orig_height,
    )


def test_move_shape_moves_off_keeps_ext(tmp_path):
    """move_shape сдвигает только позицию (off), размер фигуры не
    меняется; edge_margin issue реально закрывается ре-аудитом."""
    from deckdna.audit.basic import RULE_EDGE_MARGIN

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(
        Inches(0.05), Inches(4), Inches(3), Inches(0.5)  # прижат к левому краю
    )
    box.text_frame.text = "прижатый текст"
    src = tmp_path / "pressed.pptx"
    prs.save(src)

    before = audit_deck(src)
    margins = [i for i in before if i.rule_code == RULE_EDGE_MARGIN]
    assert margins, "фикстура должна дать edge_margin issue"

    actions = plan_repairs(before)
    moves = [a for a in actions if a.action_type == ActionType.move_shape]
    assert moves, "edge_margin должен спланировать move_shape"

    out = tmp_path / "moved.pptx"
    report = apply_repairs(src, moves, out)
    assert report.failed == 0
    assert report.applied == len(moves)

    fixed = Presentation(str(out))
    sh = fixed.slides[0].shapes[0]
    assert sh.width == box.width and sh.height == box.height  # ext не тронут
    assert sh.left > box.left  # фигура реально сдвинулась внутрь
    after = [i for i in audit_deck(out) if i.rule_code == RULE_EDGE_MARGIN]
    assert after == []


def test_move_shape_missing_bbox_xy_skipped(tmp_path):
    """move_shape без x/y в params.bbox — честный skipped."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))
    box.text_frame.text = "текст"
    src = tmp_path / "d.pptx"
    prs.save(src)

    action = RepairAction(
        schema_version="1.0",
        action_type=ActionType.move_shape,
        issue_ids=["t1"],
        target=Target(slide_id=str(slide.slide_id), shape_ids=[str(box.shape_id)]),
        params=Params(bbox=Bbox(w=0.4)),
    )
    report = apply_repairs(src, [action], tmp_path / "o.pptx")
    assert report.results[0].status == "skipped"


def test_resize_shape_stops_at_text_neighbor(tmp_path):
    """Расширение останавливается у края текстового соседа.

    Рамка, растущая в текстовую фигуру рядом, зажимается по её краю —
    apply не создаёт layout.unintended_overlap взамен закрываемого
    text.overflow. Остаточный overflow законно остаётся issue.
    """
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    a = slide.shapes.add_textbox(
        Inches(5), Inches(3), Inches(2), Inches(1.5)
    )
    a.text_frame.text = "переполненный текст"
    b = slide.shapes.add_textbox(
        Inches(7.5), Inches(3), Inches(2), Inches(1.5)
    )
    b.text_frame.text = "соседний текстовый блок"
    src = tmp_path / "nb.pptx"
    prs.save(src)

    # adversarial bbox: рамка A растёт вправо/вниз прямо в B
    action = RepairAction(
        schema_version="1.0",
        action_type=ActionType.resize_shape,
        issue_ids=["t1"],
        target=Target(
            slide_id=str(slide.slide_id), shape_ids=[str(a.shape_id)]
        ),
        params=Params(bbox=Bbox(x=0.37, y=0.4, w=0.45, h=0.8)),
    )
    out = tmp_path / "fixed.pptx"
    report = apply_repairs(src, [action], out)
    assert report.applied == 1
    assert "остановлено у соседей" in report.results[0].detail

    fixed = Presentation(str(out))
    sh_a, sh_b = fixed.slides[0].shapes[0], fixed.slides[0].shapes[1]
    assert sh_a.left + sh_a.width <= sh_b.left
    overlaps = [
        i for i in audit_deck(out)
        if i.rule_code == RULE_UNINTENDED_OVERLAP
    ]
    assert overlaps == []


SUBMISSION = Path("tests/fixtures/pptx/lct2026_submission.pptx")


def test_anchor_position_repaired_end_to_end(tmp_path):
    """Реальный сдвинутый sldNum: сдвигаем anchor-фигуру в копии
    lct2026_submission (в нём есть sldNum с declared-xfrm на layout),
    прогоняем audit → plan → apply → audit: issue исчезает."""
    if not SUBMISSION.exists():
        pytest.skip("organizer fixture not present")
    prs = Presentation(str(SUBMISSION))
    slide = prs.slides[0]
    target_shape = next(
        s for s in slide.shapes if s.shape_id == 3  # 'Номер слайда 2'
    )
    target_shape.left = int(target_shape.left) + 914400  # +1 inch >> tol
    broken = tmp_path / "broken.pptx"
    prs.save(str(broken))

    before = audit_deck(broken)
    anchor_issues = [
        i
        for i in before
        if i.rule_code == RULE_ANCHOR_POSITION
        and str(target_shape.shape_id) in (i.shape_ids or [])
    ]
    assert anchor_issues, "сдвиг sldNum не пойман — фикстура изменилась?"

    plan = plan_repairs_with_report(before)
    acts = [
        a for a in plan.actions if a.issue_ids == [anchor_issues[0].id]
    ]
    assert [a.action_type.value for a in acts] == ["resize_shape"]

    out = tmp_path / "fixed.pptx"
    report = apply_repairs(broken, plan.actions, out)
    assert report.failed == 0
    assert (
        report.by_type["resize_shape"]["applied"] >= 1
    ), report.results

    after = audit_deck(out)
    still = [
        i
        for i in after
        if i.rule_code == RULE_ANCHOR_POSITION
        and str(target_shape.shape_id) in (i.shape_ids or [])
    ]
    assert still == []
    # фигура вернулась на declared-позицию layout, а не «куда-то»
    fixed = Presentation(str(out))
    sh = next(
        s for s in fixed.slides[0].shapes if s.shape_id == 3
    )
    assert abs(sh.left - 11413507) < 2000  # declared x из layout


def test_slide_clip_repaired_end_to_end(tmp_path):
    """Реальный clip: текстовый бокс у нижнего края с оценочным
    экстентом за границей слайда → plan даёт move+shorten → после
    apply повторный аудит не выдаёт text.slide_clip по фигуре."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    sh = prs.slide_height
    box = slide.shapes.add_textbox(
        Inches(1), int(sh * 0.9), Inches(4), int(sh * 0.08)
    )
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE  # auto-size фигуры аудит пропускает
    for i in range(6):  # ~6 строк по 18pt > остаток слайда под рамкой
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = "строка текста, вылетающая за нижний край слайда"
        for r in p.runs:
            r.font.size = Pt(18)
    src = tmp_path / "clip.pptx"
    prs.save(src)

    before = audit_deck(src)
    clip_issues = [
        i
        for i in before
        if i.rule_code == RULE_SLIDE_CLIP
        and str(box.shape_id) in (i.shape_ids or [])
    ]
    assert clip_issues, "slide_clip не пойман на вылетающем тексте"

    plan = plan_repairs_with_report(before)
    acts = [a for a in plan.actions if a.issue_ids == [clip_issues[0].id]]
    assert [a.action_type.value for a in acts] == [
        "move_shape",
        "shorten_text",
    ]

    out = tmp_path / "fixed.pptx"
    report = apply_repairs(src, plan.actions, out)
    assert report.failed == 0

    after = audit_deck(out)
    still = [
        i
        for i in after
        if i.rule_code == RULE_SLIDE_CLIP
        and str(box.shape_id) in (i.shape_ids or [])
    ]
    assert still == []


def test_map_font_and_map_color_end_to_end(tmp_path):
    """map_font/map_color: >2 семейств и off-palette цвета → issue исчезает."""
    from pptx.dml.color import RGBColor

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i, font in enumerate(["Arial", "Times New Roman", "Courier New"]):
        box = slide.shapes.add_textbox(
            Inches(0.5), Inches(0.5 + i), Inches(5), Inches(0.8)
        )
        run = box.text_frame.paragraphs[0].add_run()
        run.text = f"Текст шрифтом {font} здесь"
        run.font.name = font
        run.font.size = Pt(20)
    pink = slide.shapes.add_textbox(Inches(0.5), Inches(4), Inches(5), Inches(0.8))
    run = pink.text_frame.paragraphs[0].add_run()
    run.text = "Кислотный цвет вне палитры"
    run.font.color.rgb = RGBColor(0xFF, 0x00, 0xFF)
    run.font.size = Pt(24)
    green = slide.shapes.add_textbox(Inches(0.5), Inches(5), Inches(3), Inches(1))
    green.fill.solid()
    green.fill.fore_color.rgb = RGBColor(0x00, 0xFF, 0x77)
    src = tmp_path / "bad.pptx"
    prs.save(src)

    before = audit_deck(src)
    assert any(i.rule_code == "template.font_family" for i in before)
    palette_before = [
        i for i in before if i.rule_code == "template.color_palette"
    ]
    assert len(palette_before) == 2
    # magenta (FF00FF) на белом — контраст ~3.1 < 4.5: третий map_color
    contrast_before = [
        i for i in before if i.rule_code == "accessibility.contrast"
    ]
    assert len(contrast_before) == 1

    plan = plan_repairs_with_report(before)
    types = sorted(
        a.action_type.value for a in plan.actions
        if a.action_type.value in ("map_font", "map_color")
    )
    assert types == ["map_color", "map_color", "map_color", "map_font"]

    out = tmp_path / "fixed.pptx"
    report = apply_repairs(src, plan.actions, out)
    assert report.failed == 0
    assert report.by_type["map_font"]["applied"] == 1
    # contrast-действие (error) идёт первым и за один проход чинит и
    # off-palette, и контраст на той же фигуре — её palette-действие
    # честно уходит в skipped, не применяясь второй раз.
    assert report.by_type["map_color"]["applied"] == 2
    assert report.by_type["map_color"]["skipped"] == 1

    after = audit_deck(out)
    assert not any(
        i.rule_code
        in (
            "template.font_family",
            "template.color_palette",
            "accessibility.contrast",
        )
        for i in after
    )


def test_contrast_repaired_end_to_end(tmp_path):
    """accessibility.contrast: audit → plan → apply → повторный аудит чист."""
    from pptx.dml.color import RGBColor

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "Белый текст на белом фоне — не читается совсем"
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    run.font.size = Pt(24)
    src = tmp_path / "contrast.pptx"
    prs.save(src)

    before = audit_deck(src)
    contrast_before = [
        i for i in before if i.rule_code == "accessibility.contrast"
    ]
    assert len(contrast_before) == 1

    plan = plan_repairs_with_report(before)
    assert contrast_before[0].id in plan.planned_issue_ids
    assert contrast_before[0].id not in plan.unresolved
    actions = [a for a in plan.actions if a.action_type.value == "map_color"]
    assert actions

    out = tmp_path / "contrast_fixed.pptx"
    report = apply_repairs(src, plan.actions, out)
    assert report.failed == 0
    assert report.by_type["map_color"]["applied"] >= 1

    after = audit_deck(out)
    assert not any(i.rule_code == "accessibility.contrast" for i in after)


def test_contrast_unresolvable_backdrop_skipped(tmp_path):
    """Подложка-картинка под текстом — bg не сводится к цвету → честный
    skipped, раны не перекрашиваются."""
    import io

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # 1x1 PNG растянутый под весь слайд — подложка не solid → _UNRESOLVED
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
        b"\xc0\xf0\x1f\x00\x05\x00\x01\xff\xa3\x9d\xe1\x0b\x00\x00\x00\x00IEND"
        b"\xaeB`\x82"
    )
    slide.shapes.add_picture(
        io.BytesIO(png), 0, 0, prs.slide_width, prs.slide_height
    )
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "Текст поверх картинки — фон не разрешим"
    run.font.size = Pt(24)
    src = tmp_path / "pic_bg.pptx"
    prs.save(src)

    slide_id = prs.slides[0].slide_id
    action = _action(
        ActionType.map_color, slide_id, [str(box.shape_id)]
    )
    out = tmp_path / "pic_bg_out.pptx"
    report = apply_repairs(src, [action], out)
    assert report.failed == 0
    assert report.by_type["map_color"]["skipped"] == 1


def test_contrast_already_passing_skipped(tmp_path):
    """Контраст в норме и явных off-palette цветов нет → честный skipped."""
    from pptx.dml.color import RGBColor

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "Чёрный текст на белом фоне — контраст отличный"
    run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
    run.font.size = Pt(24)
    src = tmp_path / "ok.pptx"
    prs.save(src)

    slide_id = prs.slides[0].slide_id
    action = _action(ActionType.map_color, slide_id, [str(box.shape_id)])
    out = tmp_path / "ok_out.pptx"
    report = apply_repairs(src, [action], out)
    assert report.failed == 0
    assert report.by_type["map_color"]["skipped"] == 1


def test_contrast_action_scoped_to_text_keeps_fill(tmp_path):
    """contrast-действие — typed scope=text: заливка/фон фигуры не
    трогаются, даже если заливка off-palette. Текст чинится."""
    from pptx.dml.color import RGBColor

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x00, 0xFF, 0x77)  # off-palette
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "Текст с низким контрастом на зелёном"
    run.font.color.rgb = RGBColor(0x77, 0xDD, 0x88)  # ~1.3 против fill
    run.font.size = Pt(24)
    src = tmp_path / "fill.pptx"
    prs.save(src)

    before = audit_deck(src)
    contrast = [i for i in before if i.rule_code == "accessibility.contrast"]
    assert contrast

    plan = plan_repairs_with_report(before)
    cid = {i.id for i in contrast}
    cactions = [
        a
        for a in plan.actions
        if a.action_type == ActionType.map_color
        and cid & set(a.issue_ids or [])
    ]
    assert cactions
    assert all(
        a.params.scope is not None and a.params.scope.value == "text"
        for a in cactions
    )

    out = tmp_path / "fill_fixed.pptx"
    report = apply_repairs(src, cactions, out)
    assert report.failed == 0

    fixed = Presentation(out)
    fshape = fixed.slides[0].shapes[0]
    assert fshape.fill.fore_color.rgb == RGBColor(0x00, 0xFF, 0x77)
    after = audit_deck(out)
    assert not any(i.rule_code == "accessibility.contrast" for i in after)


def test_contrast_no_better_slot_keeps_color(tmp_path, monkeypatch):
    """Ни один слот палитры не строго лучше текущего ratio → цвет не
    перезаписывается, действие честно skipped (review: no downgrade)."""
    import deckdna.repair.apply as apply_mod
    from pptx.dml.color import RGBColor

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x80, 0x80, 0x80)
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "Лучшее что есть — серый на сером"
    run.font.color.rgb = RGBColor(0x7C, 0x7C, 0x7C)
    run.font.size = Pt(24)
    src = tmp_path / "mid.pptx"
    prs.save(src)

    mids = {
        "dk1": (0x7C, 0x7C, 0x7C),
        "lt1": (0x82, 0x82, 0x82),
        "accent1": (0x80, 0x80, 0x80),
    }
    monkeypatch.setattr(
        apply_mod, "_theme_palette", lambda _slide, _ctx: dict(mids)
    )
    action = _action(
        ActionType.map_color,
        prs.slides[0].slide_id,
        [str(box.shape_id)],
        scope="text",
    )
    out = tmp_path / "mid_out.pptx"
    report = apply_repairs(src, [action], out)
    assert report.failed == 0
    assert report.by_type["map_color"]["skipped"] == 1
    assert "нет улучшения" in report.results[0].detail

    fixed = Presentation(out)
    frun = fixed.slides[0].shapes[0].text_frame.paragraphs[0].runs[0]
    assert frun.font.color.rgb == RGBColor(0x7C, 0x7C, 0x7C)


def test_map_color_palette_remap_never_downgrades(tmp_path, monkeypatch):
    """off-palette→palette перенос не ухудшает читаемость: если ближайший
    слот хуже исходного ratio, цвет рана остаётся как есть."""
    import deckdna.repair.apply as apply_mod
    from pptx.dml.color import RGBColor

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "Тёмно-синий off-palette на белом"
    run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)  # ~11:1 на белом
    run.font.size = Pt(24)
    src = tmp_path / "read.pptx"
    prs.save(src)

    mids = {
        "dk1": (0x7C, 0x7C, 0x7C),
        "lt1": (0x82, 0x82, 0x82),
        "accent1": (0x80, 0x80, 0x80),
    }
    monkeypatch.setattr(
        apply_mod, "_theme_palette", lambda _slide, _ctx: dict(mids)
    )
    action = _action(
        ActionType.map_color, prs.slides[0].slide_id, [str(box.shape_id)]
    )
    out = tmp_path / "read_out.pptx"
    report = apply_repairs(src, [action], out)
    assert report.failed == 0
    assert report.by_type["map_color"]["skipped"] == 1

    fixed = Presentation(out)
    frun = fixed.slides[0].shapes[0].text_frame.paragraphs[0].runs[0]
    assert frun.font.color.rgb == RGBColor(0x1F, 0x4E, 0x79)
