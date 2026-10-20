"""Объяснение выбора графика (ADR-033 §10): вход → профиль → задача →
почему этот тип → альтернативы. Одна форма для паспорта
(``analysis.datasets[]``), панели «Почему так» (ADR-034 §7) и заметок."""

from __future__ import annotations

from typing import Any

from deckdna.analysis.pipeline import DatasetAnalysis, ViewAnalysis, analyze_pack, source_note
from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import DeckPlan, Kind
from deckdna.planning.data_story import brief_text


def _choice(chart_type: str, reasons: list[str]) -> dict[str, Any]:
    return {"type": chart_type, "reasons": list(reasons)}


def view_explanation(
    analysis: DatasetAnalysis,
    va: ViewAnalysis,
    chosen_type: str | None = None,
) -> dict[str, Any]:
    rec = va.rec
    chosen_type = chosen_type or rec.chosen.chart_type
    chosen = next((r for r in rec.ranked if r.chart_type == chosen_type), None)
    return {
        "view_id": va.view.id,
        "title": va.view.title,
        "task": va.task.task,
        "task_ru": va.task.task_ru,
        "intent": va.task.intent,
        "aggregation": va.view.aggregation,
        # выбор пользователя вне топ-3 — без причин кода, честно
        "chosen": _choice(chosen_type, chosen.reasons if chosen else ["выбрано пользователем"]),
        "recommended": _choice(rec.chosen.chart_type, rec.chosen.reasons),
        "alternatives": [
            _choice(r.chart_type, r.reasons) for r in rec.ranked if r.chart_type != chosen_type
        ][:2],
        "rejected": [_choice(r.chart_type, r.reasons) for r in rec.rejected],
        "insights": [
            {
                "id": i.id,
                "fact": i.fact,
                "headline": i.headline,
                "meaning": i.meaning,
                "implication": i.implication,
                "kind": i.kind.value,
                "confirmed": i.confirmed,
                "needs_confirmation": i.needs_confirmation,
                "strength": i.strength,
            }
            for i in va.insights
        ],
        "source": source_note(analysis.dataset, va.view),
    }


def _chart_slides(pack: ContentPack, plan: DeckPlan | None) -> dict[str, tuple[int, str | None]]:
    """id представления → (индекс слайда, тип графика в плане)."""
    if plan is None:
        return {}
    view_of = {c.id: c.source_ref.range for c in pack.charts if c.source_ref.range}
    view_of.update({t.id: t.source_ref.range for t in pack.tables if t.source_ref.range})
    out: dict[str, tuple[int, str | None]] = {}
    for slide in plan.slides:
        for unit in slide.content_units:
            if unit.kind == Kind.chart and unit.chart_ref in view_of:
                kind = (
                    unit.chart_spec.chart_type.value
                    if unit.chart_spec and (unit.chart_spec.chart_type)
                    else None
                )
                out.setdefault(view_of[unit.chart_ref], (slide.index, kind))
            elif unit.kind == Kind.table and unit.table_ref in view_of:
                out.setdefault(view_of[unit.table_ref], (slide.index, "table"))
    return out


def analysis_report(pack: ContentPack, plan: DeckPlan | None = None) -> dict[str, Any] | None:
    """Раздел паспорта ``analysis``: по датасету — профиль и представления
    с выбором; у показанных в колоде — индекс слайда."""
    if not pack.datasets:
        return None
    from deckdna.analysis.fact_ledger import build_pack_fact_ledger

    text = brief_text(plan.brief) if plan is not None else None
    on_slides = _chart_slides(pack, plan)
    datasets = []
    analyses = analyze_pack(pack, text)
    for analysis in analyses:
        views = []
        for va in analysis.views:
            slide_index, kind = on_slides.get(va.view.id, (None, None))
            entry = view_explanation(analysis, va, kind)
            entry["slide_index"] = slide_index
            views.append(entry)
        ds_entry = {
            "id": analysis.dataset.id,
            "title": analysis.dataset.title,
            "file": analysis.dataset.file_name or analysis.dataset.source_ref.artifact_id,
            "rows": analysis.dataset.row_count,
            "profile_summary": analysis.profile.summary_ru(),
            "typable": analysis.profile.typable,
            "reason": analysis.profile.reason,
            "views": views,
        }
        if getattr(analysis, "fact_ledger", None) is not None:
            ds_entry["fact_ledger"] = analysis.fact_ledger.to_dict()
            ds_entry["warnings"] = list(analysis.fact_ledger.warnings)
        datasets.append(ds_entry)
    pack_ledger = build_pack_fact_ledger(analyses)
    return {
        "datasets": datasets,
        "fact_ledger": pack_ledger.to_dict(),
        "warnings": list(pack_ledger.warnings),
    }


__all__ = ["analysis_report", "view_explanation"]
