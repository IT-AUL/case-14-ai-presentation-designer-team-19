"""Bounded Repair — типизированные действия по issues аудита.

planner.py только предлагает RepairAction (мутации нет),
apply.py исполняет их на копии пакета (docs/pipeline/:
планирование и применение разделены).
"""

from deckdna.repair.apply import RepairApplyReport, apply_repairs
from deckdna.repair.planner import RepairPlanReport, plan_repairs, plan_repairs_with_report

__all__ = [
    "RepairApplyReport",
    "RepairPlanReport",
    "apply_repairs",
    "plan_repairs",
    "plan_repairs_with_report",
]
