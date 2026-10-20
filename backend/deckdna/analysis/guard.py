"""Детерминированная проверка фактов и текстовый guard (F-82, F-85, F-88).

Проверяет и нормализует каждый ``Insight`` перед попаданием в план:
- сверяет числа в тексте (заголовок, факт, объяснение, вывод) с исходными
  числами факта и представления с допуском округления;
- блокирует противоречие «узкий разброс» при ``max/min >= 3``, ``CV > 0.5``
  или размахе не меньше медианы (F-82);
- исправляет перевёрнутый порядок «N позиций вошли в топ-K» → «в таблице
  топ-K из N позиций» и строчные местоимения после двоеточия/тире (F-85);
- не позволяет единице измерения («Млн ₽», «%», «Шт.») становиться
  подлежащим заголовка или вывода (F-88);
- отбирает неповторяющиеся выводы («что это значит») между соседними слайдами."""

from __future__ import annotations

import re
from collections.abc import Sequence
from statistics import median, pstdev

from deckdna.analysis.profile import TableProfile
from deckdna.analysis.ranking import AnalysisConfig, load_analysis_config
from deckdna.analysis.views import DISTRIBUTION, RELATIONSHIP, DataView
from deckdna.contracts.dataset import Dataset, Insight

_NARROW_SPREAD_RE = re.compile(
    r"(?:разброс\s+узк\w*|узк\w*\s+разброс|планировать\s+от\s+типичного"
    r"|низк\w*\s+вариативност\w*|значения\s+однородн\w*)",
    re.IGNORECASE,
)

_INVERTED_TOP_RE = re.compile(
    r"(?P<prefix>:\s+|\b)(?P<total>\d[\d\s\xa0]*)\s+"
    r"(?P<noun>позиц\w*|строк\w*|запис\w*|компани\w*|товар\w*)\s+"
    r"(?:вошл\w*|попал\w*|включен\w*)\s+в\s+топ-(?P<top>\d+)\b",
    re.IGNORECASE,
)

_INVERTED_TOP_PAIR_RE = re.compile(
    r"\bтоп-(?P<first>\d+)\s+из\s+(?P<second>\d+)\b",
    re.IGNORECASE,
)

_COLON_PRONOUN_RE = re.compile(
    r"(?P<sep>[:—–]\s+)"
    r"(?P<pron>Он|Она|Оно|Они|Его|Её|Ее|Их|Ему|Ей|Им|Нём|Ней|Них|Это|Этот|Эта|Эти)\b"
)

_LEADING_UNIT_SUBJECT_RE = re.compile(
    r"^\s*(?:млн\.?\s*(?:₽|руб\.?)|тыс\.?\s*(?:₽|руб\.?)|млрд\.?\s*(?:₽|руб\.?)"
    r"|₽|руб\.?|%|п\.?\s*п\.?|шт\.?|ед\.?)\s*"
    r"(?=(?::|\s+[—–]|\s+(?:вырос\w*|снизил\w*|сократил\w*|увеличил\w*"
    r"|изменил\w*|даёт|дают|составля\w*|показыва\w*|держится|остаёт\w*"
    r"|лидиру\w*|обгоня\w*|связан\w*|разброс\w*)))",
    re.IGNORECASE,
)

_STRUCTURAL_TOKEN_RE = re.compile(
    r"(?:"
    r"\b(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?\b"
    r"|\b[1-4]\s*(?:кв\.?|квартал\w*)\b"
    r"|\bQ[1-4]\b"
    r"|(?:#|№\s*)\d+\b"
    r"|\b\d+-(?:е|го|м|й)\s+мест\w*"
    r"|\b[A-Za-zА-Яа-яЁё]+-\d+\b"
    r")",
    re.IGNORECASE,
)

_NUMBER_RE = re.compile(
    r"(?<![A-Za-zА-Яа-яЁё0-9_])"
    r"[+-]?(?:\d{1,3}(?:[\s\xa0]\d{3})+|\d+)(?:[.,]\d+)?"
    r"(?![A-Za-zА-Яа-яЁё0-9_])"
)


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def is_wide_spread(
    values: Sequence[float | None],
    config: AnalysisConfig | None = None,
) -> bool:
    """Проверить, является ли разброс значений широким (F-82).

    Срабатывает при ``max/min >= 3.0`` (для положительных рядов),
    коэффициенте вариации ``CV > 0.50``, размахе ``max - min >= |median|``
    или межквартильном размахе ``IQR >= 0.5 * |median|``."""
    clean = sorted(float(v) for v in values if v is not None)
    if len(clean) < 2:
        return False
    cfg = config or load_analysis_config()
    t = cfg.thresholds

    mn, mx = clean[0], clean[-1]
    if mx == mn:
        return False

    pos = [v for v in clean if v > 0]
    if len(pos) >= 2 and pos[0] > 0:
        if pos[-1] / pos[0] >= t.wide_spread_max_min_ratio:
            return True

    mean_val = sum(clean) / len(clean)
    if abs(mean_val) > 1e-9:
        cv = pstdev(clean) / abs(mean_val)
        if cv > t.wide_spread_cv:
            return True

    med = float(median(clean))
    if abs(med) > 1e-9:
        if (mx - mn) >= abs(med) * t.wide_spread_range_to_median:
            return True
        q1 = _quantile(clean, 0.25)
        q3 = _quantile(clean, 0.75)
        if (q3 - q1) >= abs(med) * 0.5:
            return True

    return False


def has_narrow_spread_claim(text: str | None) -> bool:
    """Содержит ли текст утверждение об узком/однородном разбросе."""
    if not text:
        return False
    return bool(_NARROW_SPREAD_RE.search(text))


def _fix_inverted_top(match: re.Match[str]) -> str:
    prefix = match.group("prefix")
    raw_total = re.sub(r"[\s\xa0]+", "", match.group("total"))
    raw_top = match.group("top")
    try:
        total_n = int(raw_total)
        top_k = int(raw_top)
    except ValueError:
        return match.group(0)
    if total_n > top_k:
        return f"{prefix}в таблице топ-{top_k} из {total_n} позиций"
    return match.group(0)


def _fix_inverted_top_pair(match: re.Match[str]) -> str:
    first = int(match.group("first"))
    second = int(match.group("second"))
    if first > second:
        return f"топ-{second} из {first}"
    return match.group(0)


def normalize_ru_text(text: str) -> str:
    """Исправить грамматические и структурные артефакты текста (F-85, F-88)."""
    if not text:
        return text
    out = _INVERTED_TOP_RE.sub(_fix_inverted_top, text)
    out = _INVERTED_TOP_PAIR_RE.sub(_fix_inverted_top_pair, out)
    out = _COLON_PRONOUN_RE.sub(
        lambda m: f"{m.group('sep')}{m.group('pron').lower()}",
        out,
    )
    out = _LEADING_UNIT_SUBJECT_RE.sub("Показатель", out)
    return out


def extract_numbers_from_text(text: str) -> list[float]:
    """Извлечь числовые значения из русского текста (без дат и ранговых меток)."""
    if not text:
        return []
    cleaned = _STRUCTURAL_TOKEN_RE.sub(" ", text)
    # «топ-6 из 240»: сохраняем оба числа 6 и 240
    cleaned = re.sub(r"\bтоп-(\d+)\b", r" \1 ", cleaned, flags=re.IGNORECASE)
    result: list[float] = []
    for m in _NUMBER_RE.finditer(cleaned):
        token = m.group(0).replace("\xa0", "").replace(" ", "").replace(",", ".")
        try:
            result.append(float(token))
        except ValueError:
            continue
    return result


def allowed_numbers_for_insight(
    insight: Insight,
    view: DataView | None = None,
    dataset: Dataset | TableProfile | None = None,
) -> list[float]:
    """Собрать множество допустимых чисел для сверки текста с данными."""
    allowed: set[float] = set()

    def _add(val: float) -> None:
        for v in (val, abs(val)):
            allowed.add(v)
            allowed.add(round(v, 0))
            allowed.add(round(v, 1))
            allowed.add(round(v, 2))

    for num in insight.numbers:
        for parsed in extract_numbers_from_text(str(num)):
            _add(parsed)

    if view is not None:
        _add(float(view.n_points))
        if view.source_rows:
            _add(float(view.source_rows))
        if view.categories:
            _add(float(len(view.categories)))
        if view.sample_headers:
            _add(float(len(view.sample_headers)))
        for note in view.notes:
            for parsed in extract_numbers_from_text(str(note)):
                _add(parsed)
        for m in view.series:
            non_null_idx = [idx for idx, v in enumerate(m.values) if v is not None]
            if len(non_null_idx) >= 2:
                _add(float(non_null_idx[-1] - non_null_idx[0] + 1))
            clean = [float(v) for v in m.values if v is not None]
            _add(float(len(clean)))
            for v in clean:
                _add(v)
            if clean:
                total = sum(clean)
                _add(total)
                _add(min(clean))
                _add(max(clean))
                _add(float(median(clean)))
                if total > 0:
                    for v in clean:
                        _add(v / total * 100.0)
                    s_desc = sorted(clean, reverse=True)
                    if len(s_desc) >= 3:
                        _add(sum(s_desc[:3]) / total * 100.0)
                    if len(s_desc) >= 2 and s_desc[1] > 0:
                        _add(s_desc[0] / s_desc[1])
                        _add(s_desc[0] - s_desc[1])
                if len(clean) >= 2 and clean[0] != 0:
                    _add((clean[-1] - clean[0]) / abs(clean[0]) * 100.0)
                    _add(clean[-1] - clean[0])
                    if clean[0] > 0:
                        _add(clean[-1] / clean[0])
        if view.x_values:
            x_clean = [float(v) for v in view.x_values if v is not None]
            _add(float(len(x_clean)))
            for v in x_clean:
                _add(v)
        if view.raw_values:
            raw = sorted(float(v) for v in view.raw_values)
            _add(float(len(raw)))
            _add(raw[0])
            _add(raw[-1])
            _add(float(median(raw)))
            _add(_quantile(raw, 0.25))
            _add(_quantile(raw, 0.75))
            if raw[0] > 0:
                _add(raw[-1] / raw[0])

    if isinstance(dataset, TableProfile):
        _add(float(dataset.n_rows))
        _add(float(dataset.n_cols))
        for col in dataset.columns:
            _add(float(col.cardinality))
            if col.missing > 0 and dataset.n_rows > 0:
                _add(float(col.missing))
                _add(col.missing_ratio * 100.0)
            nums = [float(v) for v in col.numbers if v is not None]
            if nums:
                _add(min(nums))
                _add(max(nums))
                _add(sum(nums))
                _add(float(median(nums)))
    elif dataset is not None:
        _add(float(dataset.row_count))
        _add(float(len(dataset.columns)))
        for col in dataset.columns:
            nums = [
                float(v)
                for v in col.values
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
            missing = sum(1 for v in col.values if v is None or v == "")
            if missing > 0 and dataset.row_count > 0:
                _add(float(missing))
                _add(missing / dataset.row_count * 100.0)
            if nums:
                _add(min(nums))
                _add(max(nums))
                _add(sum(nums))
                _add(float(median(nums)))

    return sorted(allowed)


def _matches_any(val: float, allowed: Sequence[float], rel_tol: float) -> bool:
    abs_val = abs(val)
    for ref in allowed:
        if abs_val < 2.0 and abs(ref) < 2.0:
            tol = max(0.05, abs(ref) * rel_tol)
        else:
            tol = max(0.55, abs(ref) * rel_tol)
        if abs(val - ref) <= tol or abs(abs_val - abs(ref)) <= tol:
            return True
    return False


def verify_text_numbers(
    text: str,
    allowed: Sequence[float],
    *,
    rel_tol: float = 0.02,
) -> list[float]:
    """Вернуть список чисел из ``text``, которые не подтверждаются данными."""
    if not text:
        return []
    extracted = extract_numbers_from_text(text)
    if not extracted:
        return []
    return [v for v in extracted if not _matches_any(v, allowed, rel_tol)]


def verify_insight_numbers(
    insight: Insight,
    view: DataView | None = None,
    dataset: Dataset | TableProfile | None = None,
    *,
    rel_tol: float = 0.02,
) -> list[float]:
    """Проверить все числа в полях ``Insight`` против данных представления."""
    allowed = allowed_numbers_for_insight(insight, view=view, dataset=dataset)
    unverified: list[float] = []
    for field_text in (
        insight.headline,
        insight.fact,
        insight.meaning or "",
        insight.implication or "",
    ):
        unverified.extend(verify_text_numbers(field_text, allowed, rel_tol=rel_tol))
    return unverified


def _view_spread_values(view: DataView | None) -> list[float]:
    if view is None:
        return []
    if view.raw_values:
        return [float(v) for v in view.raw_values]
    if view.shape == RELATIONSHIP:
        vals: list[float] = [float(v) for v in view.x_values if v is not None]
        if view.metric is not None:
            vals.extend(float(v) for v in view.metric.values if v is not None)
        return vals
    if view.metric is not None:
        return [float(v) for v in view.metric.values if v is not None]
    return []


def guard_insight(
    insight: Insight,
    view: DataView | None = None,
    dataset: Dataset | TableProfile | None = None,
    config: AnalysisConfig | None = None,
) -> Insight:
    """Нормализовать текст, устранить противоречия разброса и сверить числа."""
    cfg = config or load_analysis_config()
    rel_tol = cfg.thresholds.number_tolerance_rel

    headline = normalize_ru_text(insight.headline)
    fact = normalize_ru_text(insight.fact)
    meaning = normalize_ru_text(insight.meaning) if insight.meaning else None
    implication = normalize_ru_text(insight.implication) if insight.implication else None
    fact_type = insight.fact_type

    # Guard F-82: при широком разбросе запрещён вывод «Разброс узкий»
    vals = _view_spread_values(view)
    wide = False
    if view is not None and view.shape in (DISTRIBUTION, RELATIONSHIP):
        wide = is_wide_spread(vals, cfg)
        if view.shape == RELATIONSHIP:
            wide = wide or is_wide_spread(view.x_values, cfg)
            if view.metric is not None:
                wide = wide or is_wide_spread(view.metric.values, cfg)
    elif fact_type in ("spread", "wide_spread") and vals:
        wide = is_wide_spread(vals, cfg)

    if wide:
        if fact_type == "spread":
            fact_type = "wide_spread"
        if has_narrow_spread_claim(headline):
            headline = re.sub(
                _NARROW_SPREAD_RE,
                "широкий разброс",
                headline,
            )
        if has_narrow_spread_claim(fact):
            fact = re.sub(
                _NARROW_SPREAD_RE,
                "широкий разброс",
                fact,
            )
        if has_narrow_spread_claim(meaning):
            meaning = "Широкий разброс значений: планировать от одного среднего рискованно."
        if has_narrow_spread_claim(implication):
            implication = (
                "Стоит заложить сценарии с запасом по верхней границе, а не считать от среднего."
            )

    candidate = insight.model_copy(
        update={
            "fact_type": fact_type,
            "headline": headline,
            "fact": fact,
            "meaning": meaning,
            "implication": implication,
        }
    )

    allowed = allowed_numbers_for_insight(candidate, view=view, dataset=dataset)
    if meaning and verify_text_numbers(meaning, allowed, rel_tol=rel_tol):
        meaning = None
    if implication and verify_text_numbers(implication, allowed, rel_tol=rel_tol):
        implication = None

    return candidate.model_copy(
        update={
            "meaning": meaning,
            "implication": implication,
        }
    )


__all__ = [
    "allowed_numbers_for_insight",
    "extract_numbers_from_text",
    "guard_insight",
    "has_narrow_spread_claim",
    "is_wide_spread",
    "normalize_ru_text",
    "verify_insight_numbers",
    "verify_text_numbers",
]
