"""ADR-033: выводы из данных — профиль, задача, выбор графика, факты.

Ядро детерминированное: профиль, задачу, выбор графика и факты считает
код; модель только формулирует (``analysis/interpret.py``), и её числа
сверяются с фактами."""

from deckdna.analysis.fact_ledger import (
    Fact,
    FactKey,
    FactKeyIndex,
    FactLedger,
    are_contradictory,
    build_dataset_fact_ledger,
    build_pack_fact_ledger,
    build_view_facts,
    fact_key,
    fact_similarity,
    is_contrast,
    mmr_select,
    score_fact,
    validate_fact,
)
from deckdna.analysis.guard import (
    guard_insight,
    is_wide_spread,
    normalize_ru_text,
    verify_insight_numbers,
)
from deckdna.analysis.pipeline import (
    DatasetAnalysis,
    ViewAnalysis,
    analyze_dataset,
    analyze_pack,
    materialize,
)
from deckdna.analysis.ranking import (
    rank_view_insights,
    score_insight,
    select_slide_insights,
)
from deckdna.analysis.title_templates import (
    allowed_title_ids_for_fact,
    render_title,
)

__all__ = [
    "DatasetAnalysis",
    "Fact",
    "FactKey",
    "FactKeyIndex",
    "FactLedger",
    "ViewAnalysis",
    "allowed_title_ids_for_fact",
    "analyze_dataset",
    "analyze_pack",
    "are_contradictory",
    "build_dataset_fact_ledger",
    "build_pack_fact_ledger",
    "build_view_facts",
    "fact_key",
    "fact_similarity",
    "guard_insight",
    "is_contrast",
    "is_wide_spread",
    "materialize",
    "mmr_select",
    "normalize_ru_text",
    "rank_view_insights",
    "render_title",
    "score_fact",
    "score_insight",
    "select_slide_insights",
    "validate_fact",
    "verify_insight_numbers",
]
