"""Repair Planner: AuditIssue -> RepairAction (только планирование).

Прогон на реальных issues organizer-фикстуры + схема repair-action.
"""

import json
from pathlib import Path

import pytest
from deckdna.audit.basic import audit_deck
from deckdna.audit.issues import AuditIssue
from deckdna.contracts import to_schema_dict
from deckdna.repair import plan_repairs, plan_repairs_with_report
from jsonschema import validate

FIXTURE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "schemas" / "repair-action.schema.json").read_text()
)


@pytest.fixture(scope="module")
def issues():
    if not FIXTURE.exists():
        pytest.skip("organizer fixture not present")
    return audit_deck(FIXTURE)


def _issue(rule: str, **kw) -> AuditIssue:
    base = dict(
        rule_code=rule,
        severity="error",
        message="m",
        audit_run_id="run-t",
        id=f"run-t:{rule}:s0:1",
        slide_id="256",
        slide_index=0,
        shape_ids=["5"],
        measured_value=200.0,
        threshold=100.0,
        evidence=[{"kind": "geometry", "ref": "slide[0]", "detail": "height"}],
    )
    base.update(kw)
    return AuditIssue(**base)


class TestRealIssues:
    def test_every_action_matches_schema(self, issues):
        for action in plan_repairs(issues):
            validate(instance=to_schema_dict(action), schema=SCHEMA)

    def test_mapping_and_targeting(self, issues):
        report = plan_repairs_with_report(issues)
        by_rule = {}
        for i in issues:
            by_rule.setdefault(i.rule_code, []).append(i)
        by_type = {}
        for a in report.actions:
            by_type.setdefault(a.action_type.value, []).append(a)
        # известные правила замаплены 1:1 по типам
        assert len(by_type.get("resize_shape", [])) == len(
            by_rule.get("text.overflow", [])
        ) + len(by_rule.get("layout.out_of_bounds", []))
        assert len(by_type.get("recrop_image", [])) == len(by_rule.get("image.aspect_ratio", []))
        assert len(by_type.get("merge_slide", [])) == len(
            by_rule.get("integrity.empty_slide", [])
        ) + len(by_rule.get("integrity.duplicate_slide", []))
        assert len(by_type.get("remove_placeholder", [])) == len(
            by_rule.get("integrity.placeholder_text", [])
        )
        # каждое действие указывает слайд и закрывает issue
        for a in report.actions:
            assert a.target.slide_id
            assert a.issue_ids
        # все issue либо спланированы, либо честно unresolved
        covered = set(report.planned_issue_ids) | set(report.unresolved)
        assert covered == {i.id for i in issues}

    def test_overflow_resize_params_are_real(self, issues):
        report = plan_repairs_with_report(issues)
        overflow = [
            a for a in report.actions if a.action_type.value == "resize_shape"
        ]
        assert overflow, "на vk_tech_template нет overflow issues — фикстура изменилась?"
        for a in overflow:
            # bbox расширен по оси переполнения, не выдуман
            assert a.params.bbox is not None
            assert a.params.bbox.w <= 1.0 and a.params.bbox.h <= 1.0

    def test_blockers_first(self, issues):
        report = plan_repairs_with_report(issues)
        order = {i.id: i.severity for i in issues}
        ranks = [
            {"blocker": 0, "error": 1, "warning": 2, "info": 3}[order[a.issue_ids[0]]]
            for a in report.actions
            if a.issue_ids
        ]
        assert ranks == sorted(ranks)


class TestSynthetic:
    def test_unknown_rule_is_unresolved_not_invented(self):
        issues = [_issue("layout.unknown_rule")]
        report = plan_repairs_with_report(issues)
        assert report.actions == []
        assert report.unresolved[issues[0].id].startswith("нет маппинга")
        assert report.coverage == 0.0

    def test_missing_slide_id_skipped(self):
        issues = [_issue("text.overflow", slide_id=None)]
        report = plan_repairs_with_report(issues)
        assert report.actions == []
        assert "slide_id" in report.unresolved[issues[0].id]

    def test_not_repairable_skipped(self):
        issues = [_issue("image.aspect_ratio", repairable=False)]
        report = plan_repairs_with_report(issues)
        assert report.actions == []
        assert "repairable=False" in report.unresolved[issues[0].id]

    def test_overflow_horizontal_and_vertical(self):
        v = _issue(
            "text.overflow",
            evidence=[{"kind": "geometry", "ref": "r", "detail": "usable height"}],
            bbox={"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.2},
        )
        h = _issue(
            "text.overflow",
            evidence=[{"kind": "geometry", "ref": "r", "detail": "usable width"}],
            bbox={"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.5},
            id="run-t:text.overflow:s0:2",
        )
        acts = plan_repairs([v, h])
        by_type = [a.action_type.value for a in acts]
        # resize + самопроверяющийся shorten_text-fallback за каждым
        assert by_type == [
            "resize_shape",
            "shorten_text",
            "resize_shape",
            "shorten_text",
        ]
        rv, rh = acts[0], acts[2]
        assert rv.params.bbox.h == pytest.approx(0.4)  # h *= 200/100
        assert rh.params.bbox.w == pytest.approx(0.4)  # w *= 200/100
        # shorten_text — адресат тот же shape, params пусты (самомерение)
        assert acts[1].target == rv.target and acts[3].target == rh.target
        assert acts[1].params.bbox is None

    def test_merge_slide_into_previous(self):
        issue = _issue("integrity.empty_slide", slide_index=3, measured_value=0, threshold=1)
        (a,) = plan_repairs([issue])
        assert a.action_type.value == "merge_slide"
        assert a.params.new_index == 2

    def test_raster_only_flagged_not_fixed(self):
        issue = _issue("editability.raster_only", severity="blocker")
        (a,) = plan_repairs([issue])
        assert a.action_type.value == "native_rebuild"
        assert any("требует внимания" in p for p in a.preconditions)

    def test_out_of_bounds_clamped_into_slide(self):
        """out_of_bounds → resize_shape с bbox, зажатым в [0,1]."""
        issue = _issue(
            "layout.out_of_bounds",
            bbox={"x": 0.8, "y": 0.1, "w": 0.5, "h": 0.3},
        )
        (a,) = plan_repairs([issue])
        assert a.action_type.value == "resize_shape"
        bb = a.params.bbox
        assert 0 <= bb.x and 0 <= bb.y
        assert bb.x + bb.w <= 1.0 and bb.y + bb.h <= 1.0
        assert bb.w == pytest.approx(0.2)  # 0.5 обрезано до 1 - 0.8

    def test_out_of_bounds_negative_origin(self):
        """Отрицательная позиция сдвигается к 0, размер сохраняется."""
        issue = _issue(
            "layout.out_of_bounds",
            bbox={"x": -0.1, "y": -0.05, "w": 0.3, "h": 0.3},
            id="run-t:layout.out_of_bounds:s0:9",
        )
        (a,) = plan_repairs([issue])
        bb = a.params.bbox
        assert bb.x == 0.0 and bb.y == 0.0
        assert bb.w == pytest.approx(0.3) and bb.h == pytest.approx(0.3)

    def test_duplicate_slide_merges_into_original(self):
        """duplicate_slide → merge_slide в индекс исходного слайда
        (парсится из evidence 'vs slide[N]')."""
        issue = _issue(
            "integrity.duplicate_slide",
            severity="warning",
            slide_index=4,
            evidence=[
                {
                    "kind": "text",
                    "ref": "slide[4]",
                    "detail": "jaccard similarity = 97% vs slide[1]",
                }
            ],
        )
        (a,) = plan_repairs([issue])
        assert a.action_type.value == "merge_slide"
        assert a.params.new_index == 1  # оригинал, не сосед
        assert a.target.slide_id == issue.slide_id
        assert any("jaccard" in p or "пользователя" in p for p in a.preconditions)


class TestRealNewRules:
    """На реальных organizer-issues новые коды тоже покрываются."""

    def test_out_of_bounds_real_actions_in_bounds(self, issues):
        report = plan_repairs_with_report(issues)
        oob = [
            a
            for a in report.actions
            if "out_of_bounds" in " ".join(a.issue_ids or [])
        ]
        if not oob:
            pytest.skip("на фикстуре нет out_of_bounds issues")
        for a in oob:
            assert a.action_type.value == "resize_shape"
            bb = a.params.bbox
            assert 0 <= bb.x and 0 <= bb.y
            assert bb.x + bb.w <= 1.0 + 1e-9 and bb.y + bb.h <= 1.0 + 1e-9
            assert a.target.slide_id and a.issue_ids

    def test_duplicate_slide_real_merge_into_original(self, issues):
        report = plan_repairs_with_report(issues)
        dup = [
            a
            for a in report.actions
            if "duplicate_slide" in " ".join(a.issue_ids or [])
        ]
        if not dup:
            pytest.skip("на фикстуре нет duplicate_slide issues")
        for a in dup:
            assert a.action_type.value == "merge_slide"
            assert a.params.new_index is not None
            # merge в индекс исходного слайда — он строго раньше повтора
            assert a.params.new_index < 54

    def test_new_rule_codes_not_unresolved(self, issues):
        report = plan_repairs_with_report(issues)
        new_codes = {"layout.out_of_bounds", "integrity.duplicate_slide"}
        still_open = {
            i.rule_code
            for i in issues
            if i.id in report.unresolved and i.rule_code in new_codes
        }
        assert still_open == set()

    def test_placeholder_text_remove(self):
        """placeholder_text → remove_placeholder на фигуре с заглушкой."""
        issue = _issue(
            "integrity.placeholder_text",
            evidence=[
                {
                    "kind": "text",
                    "ref": "slide[0]",
                    "detail": "whole-frame stock phrase: 'заголовок'",
                }
            ],
        )
        (a,) = plan_repairs([issue])
        assert a.action_type.value == "remove_placeholder"
        assert a.target.shape_ids == ["5"]
        assert "placeholder" in " ".join(a.preconditions)

    def test_edge_margin_moves_to_margin(self):
        """edge_margin → move_shape: сдвиг прижатой стороны ровно к
        EDGE_MARGIN, размер и вторая ось не меняются."""
        for side, exp in (
            ("left", ("x", 0.03)),
            ("right", ("x", 0.77)),   # 1 - 0.03 - w(0.2)
            ("top", ("y", 0.03)),
            ("bottom", ("y", 0.87)),  # 1 - 0.03 - h(0.1)
        ):
            issue = _issue(
                "layout.edge_margin",
                severity="warning",
                bbox={"x": 0.01, "y": 0.4, "w": 0.2, "h": 0.1},
                id=f"run-t:edge:{side}",
                evidence=[
                    {
                        "kind": "geometry",
                        "ref": "slide[0]",
                        "detail": f"{side} edge gap = 1.0% of slide "
                        f"{'width' if side in ('left', 'right') else 'height'} "
                        "< 3.0% margin band",
                    }
                ],
            )
            (a,) = plan_repairs([issue])
            assert a.action_type.value == "move_shape", side
            coord, val = exp
            assert getattr(a.params.bbox, coord) == pytest.approx(val), side
            # вторая ось и размеры не трогаем — только позиция
            other = "y" if coord == "x" else "x"
            assert getattr(a.params.bbox, other) is None
            assert a.params.bbox.w is None and a.params.bbox.h is None

    def test_edge_margin_too_wide_unresolved(self):
        """Фигура шире внутренней полосы (w > 1 − 2·margin) — честный
        unresolved, а не выдуманный сдвиг."""
        issue = _issue(
            "layout.edge_margin",
            severity="warning",
            bbox={"x": 0.005, "y": 0.4, "w": 0.97, "h": 0.1},
            id="run-t:edge:wide",
            evidence=[
                {"kind": "geometry", "ref": "slide[0]", "detail": "left edge gap = 0.5%"}
            ],
        )
        report = plan_repairs_with_report([issue])
        assert issue.id in report.unresolved
        assert report.actions == []

    def test_unintended_overlap_explicitly_unresolved(self):
        """unintended_overlap — нет детерминированного fix: причина
        unresolved говорит, что это layout-решение, а не типизированное
        действие."""
        issue = _issue(
            "layout.unintended_overlap",
            shape_ids=["5", "9"],
            id="run-t:overlap:1",
        )
        report = plan_repairs_with_report([issue])
        assert issue.id in report.unresolved
        assert "layout" in report.unresolved[issue.id]
        assert report.actions == []

    def test_placeholder_strong_marker_warns_content(self):
        """STRONG-маркер внутри длинного текста — precondition честно
        предупреждает, что удаление может затронуть контент."""
        issue = _issue(
            "integrity.placeholder_text",
            evidence=[
                {"kind": "text", "ref": "slide[0]", "detail": "strong marker: 'todo доделать'"}
            ],
            id="run-t:integrity.placeholder_text:s0:2",
        )
        (a,) = plan_repairs([issue])
        assert a.action_type.value == "remove_placeholder"
        assert any("настоящий контент" in p for p in a.preconditions)


class TestAnchorPosition:
    """template.anchor_position → resize_shape на declared-xfrm."""

    DETAIL = (
        "own xfrm=(500000, 400000, 2000000, 1000000) vs "
        "declared=(400000, 800000, 2000000, 800000); "
        "deviated: x Δ7.9pt, y Δ31.5pt, cy Δ15.7pt (tol 2.0pt)"
    )
    # sw_emu = own_cx / bbox.w = 2000000/0.5 = 4000000,
    # sh_emu = own_cy / bbox.h = 1000000/0.25 = 4000000
    BBOX = {"x": 0.125, "y": 0.1, "w": 0.5, "h": 0.25}

    def _issue(self, **kw) -> AuditIssue:
        base = dict(
            bbox=dict(self.BBOX),
            measured_value=31.5,
            threshold=2.0,
            evidence=[
                {"kind": "geometry", "ref": "slide[0]", "detail": self.DETAIL}
            ],
        )
        base.update(kw)
        return _issue("template.anchor_position", **base)

    def test_realigns_all_four_components(self):
        (a,) = plan_repairs([self._issue()])
        assert a.action_type.value == "resize_shape"
        bb = a.params.bbox
        # declared=(400000, 800000, 2000000, 800000) на слайде 4Mx4M EMU
        assert bb.x == pytest.approx(0.1)
        assert bb.y == pytest.approx(0.2)
        assert bb.w == pytest.approx(0.5)
        assert bb.h == pytest.approx(0.2)

    def test_no_detail_is_unresolved(self):
        issue = self._issue(
            evidence=[{"kind": "geometry", "ref": "s", "detail": "no xfrm data"}],
            id="run-t:anchor:nodetail",
        )
        report = plan_repairs_with_report([issue])
        assert report.actions == []
        assert issue.id in report.unresolved

    def test_no_bbox_is_unresolved(self):
        issue = self._issue(bbox=None, id="run-t:anchor:nobbox")
        report = plan_repairs_with_report([issue])
        assert report.actions == []
        assert issue.id in report.unresolved


class TestSlideClip:
    """text.slide_clip → move_shape (однозначное направление) + shorten_text."""

    def _detail(self, anchor: str, x_pt: float, y_pt: float) -> list[dict]:
        return [
            {
                "kind": "geometry",
                "ref": "slide[0]",
                "detail": (
                    f"anchor={anchor} wrap=True; text extent beyond slide edge "
                    f"x={x_pt}pt y={y_pt}pt"
                ),
            }
        ]

    def _issue(self, **kw) -> AuditIssue:
        base = dict(
            bbox={"x": 0.1, "y": 0.6, "w": 0.5, "h": 0.3},
            measured_value=0.05,
            threshold=0.005,
            evidence=self._detail("t", 0.0, 25.4),
        )
        base.update(kw)
        return _issue("text.slide_clip", **base)

    def test_bottom_clip_moves_up_then_shortens(self):
        acts = plan_repairs([self._issue()])
        assert [a.action_type.value for a in acts] == [
            "move_shape",
            "shorten_text",
        ]
        assert acts[0].params.bbox.y == pytest.approx(0.55)  # 0.6 - 0.05
        assert acts[0].params.bbox.x is None  # другая ось не трогается

    def test_top_clip_anchor_b_moves_down(self):
        issue = self._issue(
            evidence=self._detail("b", 0.0, 25.4),
            bbox={"x": 0.1, "y": 0.02, "w": 0.5, "h": 0.3},
            id="run-t:clip:b",
        )
        acts = plan_repairs([issue])
        assert acts[0].action_type.value == "move_shape"
        assert acts[0].params.bbox.y == pytest.approx(0.07)  # 0.02 + 0.05

    def test_ctr_only_shortens(self):
        """anchor=ctr — сторона вылета неоднозначна, move не выдумывается."""
        issue = self._issue(
            evidence=self._detail("ctr", 0.0, 25.4), id="run-t:clip:ctr"
        )
        acts = plan_repairs([issue])
        assert [a.action_type.value for a in acts] == ["shorten_text"]

    def test_horizontal_clip_only_shortens(self):
        """wrap=none — направление по algn, который в evidence нет."""
        issue = self._issue(
            evidence=self._detail("t", 40.0, 0.0), id="run-t:clip:x"
        )
        acts = plan_repairs([issue])
        assert [a.action_type.value for a in acts] == ["shorten_text"]

    def test_mixed_axes_only_shortens(self):
        """Вылет по обеим осям: measured_value — не y-компонента,
        move по measured был бы преувеличен — честный shorten."""
        issue = self._issue(
            evidence=self._detail("t", 40.0, 25.4), id="run-t:clip:xy"
        )
        acts = plan_repairs([issue])
        assert [a.action_type.value for a in acts] == ["shorten_text"]
