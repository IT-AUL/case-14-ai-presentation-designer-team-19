"""Исходы repair и fingerprint (contract D2): отчёт должен совпадать с фактом."""

from __future__ import annotations

from deckdna.audit.issues import AuditIssue
from deckdna.contracts.repair_action import ActionType, Bbox, Params, RepairAction, Target
from deckdna.repair import outcomes as oc
from deckdna.repair.apply import ActionResult, RepairApplyReport
from deckdna.repair.planner import RepairPlanReport


def _issue(id_: str, rule="text.overflow", slide_id="258", shapes=("585",), run="a") -> AuditIssue:
    return AuditIssue(
        rule_code=rule,
        severity="error",
        message="m",
        audit_run_id=run,
        id=id_,
        slide_id=slide_id,
        slide_index=2,
        shape_ids=list(shapes),
    )


def _action(issue_id: str, kind=ActionType.resize_shape) -> RepairAction:
    return RepairAction(
        schema_version="1.0",
        action_type=kind,
        issue_ids=[issue_id],
        target=Target(slide_id="258", shape_ids=["585"]),
        params=Params(bbox=Bbox(x=0.1, y=0.1, w=0.5, h=0.3)),
    )


def _report(*statuses: str) -> RepairApplyReport:
    rep = RepairApplyReport(out_path="x")
    for i, status in enumerate(statuses):
        rep.results.append(
            ActionResult(index=i, action_type="resize_shape", status=status, detail=f"d{i}")
        )
    return rep


class TestFingerprint:
    def test_stable_across_ids_and_runs(self):
        assert oc.fingerprint(_issue("a:1", run="r1")) == oc.fingerprint(_issue("b:9", run="r2"))

    def test_differs_by_rule_slide_or_shape(self):
        base = oc.fingerprint(_issue("x"))
        assert base != oc.fingerprint(_issue("x", rule="text.slide_clip"))
        assert base != oc.fingerprint(_issue("x", slide_id="259"))
        assert base != oc.fingerprint(_issue("x", shapes=("586",)))

    def test_slide_id_not_index_survives_a_deleted_neighbour(self):
        moved = _issue("x")
        moved.slide_index = 1  # соседний слайд удалён — индекс сдвинулся
        assert oc.fingerprint(moved) == oc.fingerprint(_issue("x"))

    def test_shape_order_is_irrelevant(self):
        assert oc.fingerprint(_issue("x", shapes=("1", "2"))) == oc.fingerprint(
            _issue("x", shapes=("2", "1"))
        )


class TestBuildOutcomes:
    def test_fixed_only_when_applied_and_gone_from_reaudit(self):
        issue = _issue("i1")
        plan = RepairPlanReport(actions=[_action("i1")], planned_issue_ids=["i1"])
        out = oc.build_outcomes([issue], plan, _report("applied"), fresh=[])
        assert out[0].status == oc.FIXED and out[0].action == "resize_shape"

    def test_applied_but_still_flagged_is_a_failure_not_a_success(self):
        issue = _issue("i1")
        plan = RepairPlanReport(actions=[_action("i1")], planned_issue_ids=["i1"])
        out = oc.build_outcomes([issue], plan, _report("applied"), fresh=[_issue("new-id")])
        assert out[0].status == oc.FAILED
        assert "повторный аудит" in out[0].reason

    def test_gone_without_any_applied_action_is_not_credited(self):
        issue = _issue("i1")
        plan = RepairPlanReport(actions=[_action("i1")], planned_issue_ids=["i1"])
        out = oc.build_outcomes([issue], plan, _report("skipped"), fresh=[])
        assert out[0].status == oc.SKIPPED

    def test_executor_failure_carries_its_reason(self):
        issue = _issue("i1")
        plan = RepairPlanReport(actions=[_action("i1")], planned_issue_ids=["i1"])
        out = oc.build_outcomes([issue], plan, _report("failed"), fresh=[_issue("z")])
        assert out[0].status == oc.FAILED and out[0].reason == "d0"

    def test_unresolved_by_planner_is_skipped_with_reason(self):
        issue = _issue("i1", rule="template.font_scale")
        plan = RepairPlanReport(actions=[], planned_issue_ids=[], unresolved={"i1": "нет маппинга"})
        out = oc.build_outcomes([issue], plan, _report(), fresh=[issue])
        assert out[0].status == oc.SKIPPED and out[0].reason == "нет маппинга"
        assert oc.count_outcomes(out, plan) == {
            "applied": 0,
            "failed": 0,
            "skipped": 0,
            "unresolved": 1,
        }

    def test_counts_partition_the_selection(self):
        a, b, c = _issue("a", shapes=("1",)), _issue("b", shapes=("2",)), _issue("c", shapes=("3",))
        plan = RepairPlanReport(
            actions=[_action("a"), _action("b")],
            planned_issue_ids=["a", "b"],
            unresolved={"c": "нет"},
        )
        out = oc.build_outcomes([a, b, c], plan, _report("applied", "applied"), fresh=[b])
        counts = oc.count_outcomes(out, plan)
        assert counts == {"applied": 1, "failed": 1, "skipped": 0, "unresolved": 1}
        assert sum(counts.values()) == 3

    def test_dry_run_plans_without_claiming_success(self):
        issue = _issue("i1")
        plan = RepairPlanReport(actions=[_action("i1")], planned_issue_ids=["i1"])
        out = oc.plan_outcomes([issue], plan)
        assert out[0].status == oc.PLANNED
        assert "Расширить рамку" in out[0].summary


class TestFixPreview:
    def test_repairable_issue_gets_a_russian_description(self):
        issue = _issue("i1")
        issue.evidence = [
            {"kind": "geometry", "detail": "estimated text height ≈140.0pt > usable height 13.3pt"}
        ]
        issue.bbox = {"x": 0.05, "y": 0.4, "w": 0.14, "h": 0.03}
        preview = oc.fix_preview(issue)
        assert preview and preview["description_ru"]
        assert preview["actions"][0] == "resize_shape"
        assert preview["title_ru"]

    def test_non_repairable_has_no_preview(self):
        issue = _issue("i1", rule="text.font_floor")
        issue.repairable = False
        assert oc.fix_preview(issue) is None
