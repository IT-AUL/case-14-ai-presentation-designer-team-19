# hand-maintained (ADR-033 §1, §4, §5): schemas/dataset.schema.json

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from deckdna.contracts.content_pack import SourceRef


class DatasetColumn(BaseModel):
    """Колонка датасета с сырыми значениями (как в файле, после чтения)."""

    name: str
    values: list[str | float | None]


class Dataset(BaseModel):
    """Табличные данные пользователя (CSV, XLSX, табличный JSON, таблица в
    тексте). Профиль, задача, выбор графика и выводы считаются по нему
    кодом (``deckdna.analysis``) и не зависят от формата файла."""

    id: str
    title: str
    columns: list[DatasetColumn]
    row_count: int
    source_ref: SourceRef
    # имя файла пользователя (artifact_id в API — служебный id)
    file_name: str | None = None
    # текст вокруг таблицы и подпись: цель («показать рост»), контекст
    context_text: str | None = None


class InsightKind(Enum):
    fact = "fact"
    recommendation = "recommendation"


class Insight(BaseModel):
    """Вывод «факт → что значит → что следует» (ADR-033 §5).

    ``fact`` и ``headline`` считает код; ``meaning`` и ``implication``
    формулирует модель, числа в них сверяются с фактом. Следствие —
    предложение: ``needs_confirmation`` до подтверждения пользователем."""

    id: str
    dataset_id: str
    view_id: str | None = None
    kind: InsightKind = InsightKind.fact
    # шаблон факта: growth | decline | peak | leader | concentration |
    # spread | outlier | correlation | missing | share
    fact_type: str | None = None
    fact: str
    headline: str | None = None
    meaning: str | None = None
    implication: str | None = None
    strength: float = Field(ge=0.0, le=1.0)
    confirmed: bool = False
    needs_confirmation: bool = False
    # ссылки на ячейки: «файл · лист · колонка, строки a–b»
    evidence: list[str] = []
    # числа факта как в тексте — для сверки формулировок модели
    numbers: list[str] = []


class RejectedViz(BaseModel):
    chart_type: str
    reasons: list[str]


class VizRecommendation(BaseModel):
    """Один вариант визуализации с человекочитаемыми причинами."""

    chart_type: str
    score: float
    reasons: list[str]


def _rebuild_content_pack() -> None:
    from deckdna.contracts import content_pack as _cp

    if "Dataset" not in vars(_cp):
        vars(_cp)["Dataset"] = Dataset
        vars(_cp)["Insight"] = Insight
        _cp.ContentPack.model_rebuild()


_rebuild_content_pack()

__all__ = [
    "Dataset",
    "DatasetColumn",
    "Insight",
    "InsightKind",
    "RejectedViz",
    "VizRecommendation",
]
