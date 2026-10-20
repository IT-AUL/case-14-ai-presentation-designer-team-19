"""Детерминированные шаблоны заголовков для FactLedger (выжимка 19.10 §1, §2).

Модель или планировщик выбирает только ``realization_id`` из
``fact.allowed_title_ids``; все числа, единицы измерения и периоды
подставляет код в русском формате («23%», «млн ₽», «с ноября по декабрь»)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from deckdna.analysis.values import (
    fmt_number,
    fmt_scaled,
    fmt_signed_pct,
    parse_period,
    plural,
    times_phrase,
    verb,
)

if TYPE_CHECKING:
    from deckdna.analysis.fact_ledger import Fact

_METRIC_RU_MAP: dict[str, str] = {
    "sales": "Продажи",
    "revenue": "Выручка",
    "orders": "Заказы",
    "profit": "Прибыль",
    "ebitda": "EBITDA",
    "cost": "Расходы",
    "costs": "Расходы",
    "expenses": "Затраты",
    "ad_spend": "Расходы на рекламу",
    "clients": "Клиенты",
    "customers": "Клиенты",
    "users": "Пользователи",
    "traffic": "Трафик",
    "conversion": "Конверсия",
    "loss": "Убыток",
    "nps": "NPS",
    "stock": "Остаток",
    "price": "Цена",
    "check": "Средний чек",
    "order_value": "Сумма заказа",
}

_GENITIVE_MAP: dict[str, str] = {
    "выручка": "выручки",
    "продажи": "продаж",
    "сумма": "суммы",
    "заказы": "заказов",
    "доход": "дохода",
    "прибыль": "прибыли",
    "расходы": "расходов",
    "затраты": "затрат",
    "клиенты": "клиентов",
    "пользователи": "пользователей",
    "трафик": "трафика",
    "оборот": "оборота",
    "число записей": "записей",
    "количество": "количества",
    "бюджет": "бюджета",
    "конверсия": "конверсии",
    "остаток": "остатка",
    "убыток": "убытка",
    "показатель": "показателя",
}

_MONTH_GENITIVE_RU: dict[int, str] = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

_MONTH_ACCUSATIVE_RU: dict[int, str] = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}

_MONTH_PREPOSITIONAL_RU: dict[int, str] = {
    1: "январе",
    2: "феврале",
    3: "марте",
    4: "апреле",
    5: "мае",
    6: "июне",
    7: "июле",
    8: "августе",
    9: "сентябре",
    10: "октябре",
    11: "ноябре",
    12: "декабре",
}

_MONTH_NOMINATIVE_RU: dict[int, str] = _MONTH_ACCUSATIVE_RU

_UNIT_NORMALIZE_RE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*млн\.?\s*(?:руб\.?|р\.?|₽)\s*$", re.I), "млн ₽"),
    (re.compile(r"^\s*тыс\.?\s*(?:руб\.?|р\.?|₽)\s*$", re.I), "тыс. ₽"),
    (re.compile(r"^\s*млрд\.?\s*(?:руб\.?|р\.?|₽)\s*$", re.I), "млрд ₽"),
    (re.compile(r"^\s*(?:руб\.?|р\.?|rub)\s*$", re.I), "₽"),
    (re.compile(r"^\s*(?:шт\.?|штук|pcs)\s*$", re.I), "шт."),
    (re.compile(r"^\s*(?:тыс|тыс\.)\s*$", re.I), "тыс."),
    (re.compile(r"^\s*(?:млн|млн\.)\s*$", re.I), "млн"),
    (re.compile(r"^\s*(?:млрд|млрд\.)\s*$", re.I), "млрд"),
]

_UNIT_ONLY_RE = re.compile(
    r"^\s*(?:млн\.?|тыс\.?|млрд\.?)?\s*(?:₽|руб\.?|р\.?|%|п\.?\s*п\.?|шт\.?|ед\.?|\$|€)\s*$",
    re.I,
)
_UNIT_SUFFIX_RE = re.compile(
    r"[\s,]+\((?:млн|млрд|тыс\.?|в)\s[^)]*\)$"
    r"|[\s,]+(?:млн|млрд|тыс\.?)\s*(?:₽|руб\.?|%|шт\.?|ед\.?|чел\.?|usd|eur|\$|€)\s*$"
    r"|[\s,]+(?:₽|руб\.?|%|шт\.?|ед\.?|чел\.?)\s*$",
    re.I,
)


def format_unit_ru(unit: str | None) -> str | None:
    """Нормализовать единицу измерения в русский формат («млн руб.» → «млн ₽»)."""
    if unit is None:
        return None
    clean = unit.strip()
    if not clean:
        return None
    for pattern, repl in _UNIT_NORMALIZE_RE:
        if pattern.match(clean):
            return repl
    return clean


def format_metric_title_ru(metric: str) -> str:
    """Человекочитаемое русское название метрики без единицы измерения в конце."""
    raw = (metric or "").strip()
    if not raw or _UNIT_ONLY_RE.match(raw):
        return "Показатель"
    low = raw.casefold()
    if low in _METRIC_RU_MAP:
        return _METRIC_RU_MAP[low]
    stripped = _UNIT_SUFFIX_RE.sub("", raw).strip(" ,")
    if stripped.casefold() in _METRIC_RU_MAP:
        return _METRIC_RU_MAP[stripped.casefold()]
    out = stripped or raw
    return out[0].upper() + out[1:] if out else "Показатель"


def format_metric_genitive_ru(metric: str) -> str:
    """Родительный падеж метрики («Выручка» → «выручки»)."""
    title = format_metric_title_ru(metric)
    low = title.casefold()
    if low in _GENITIVE_MAP:
        return _GENITIVE_MAP[low]
    return f"показателя «{title}»"


def format_value_ru(value: float | int, unit: str | None = None) -> str:
    """Русское форматирование числа с единицей («123 млн ₽», «23%»)."""
    norm_unit = format_unit_ru(unit)
    return fmt_scaled(float(value), norm_unit).replace("\u00a0", " ")


def format_pct_ru(value: float | int, *, signed: bool = False, decimals: int | None = None) -> str:
    """Русское форматирование процентов («23%», «+23%», «12,5%»)."""
    val = float(value)
    if signed:
        d = decimals if decimals is not None else (0 if abs(val) >= 10 or val.is_integer() else 1)
        return fmt_signed_pct(val, decimals=d).replace("\u00a0", " ")
    if decimals is None:
        decimals = 0 if abs(val) >= 10 or val.is_integer() else 1
    return f"{fmt_number(val, decimals=decimals).replace('\u00a0', ' ')}%"


def _strip_period_Annotations(raw: str) -> str:
    return re.sub(r"\s*(?:\([^)]*\)|\*+)\s*$", "", raw.strip())


def format_period_span_ru(comparison: dict[str, Any] | None) -> str:
    """Русская формулировка периода сравнения: «с ноября по декабрь»,
    «с января 2024 по декабрь 2025», «с 1 по 4 кв. 2025», «с 2024 по 2025 год»."""
    if not comparison:
        return "за период"
    from_raw = comparison.get("from")
    to_raw = comparison.get("to")
    if from_raw is not None and to_raw is not None:
        p_from = parse_period(_strip_period_Annotations(str(from_raw)))
        p_to = parse_period(_strip_period_Annotations(str(to_raw)))
        if p_from is not None and p_to is not None and p_from.grain == p_to.grain:
            y1, m1, _ = p_from.key
            y2, m2, _ = p_to.key
            if p_from.grain == "month":
                prep = "со" if m1 == 2 and False else "с"
                m1_gen = _MONTH_GENITIVE_RU.get(m1, p_from.label)
                m2_acc = _MONTH_ACCUSATIVE_RU.get(m2, p_to.label)
                if y1 == y2:
                    return f"{prep} {m1_gen} по {m2_acc}"
                return f"{prep} {m1_gen} {y1} по {m2_acc} {y2}"
            if p_from.grain == "quarter":
                q1 = (m1 - 1) // 3 + 1
                q2 = (m2 - 1) // 3 + 1
                prep = "со" if q1 == 2 else "с"
                if y1 == y2:
                    return f"{prep} {q1} по {q2} кв. {y1}"
                return f"{prep} {q1} кв. {y1} по {q2} кв. {y2}"
            if p_from.grain == "year":
                prep = "со" if str(y1).startswith("2") and y1 == 2 else "с"
                return f"{prep} {y1} по {y2} год"
            return f"с {p_from.label} по {p_to.label}"
        return f"с {from_raw} по {to_raw}"
    period_raw = comparison.get("period")
    if period_raw is not None:
        p = parse_period(_strip_period_Annotations(str(period_raw)))
        if p is not None and p.grain == "month":
            y, m, _ = p.key
            return f"в {_MONTH_PREPOSITIONAL_RU.get(m, p.label)} {y}"
        if p is not None and p.grain == "year":
            return f"в {p.key[0]} году"
        return f"за {period_raw}"
    return "за период"


def _prep_s(num_text: str) -> str:
    """Предлог «с» или «со» перед числительным («со 100», «с 50»)."""
    clean = num_text.lstrip("−+- ")
    if clean.startswith(("100", "2 ", "2,", "200")):
        return "со"
    return "с"


def _ongoing_verb(kind: str, metric_ru: str) -> str:
    """Глагол несовершенного вида для «устойчиво росло» / «росло каждый месяц»."""
    from deckdna.analysis.values import head_gender

    g = head_gender(metric_ru)
    if kind == "grow":
        return {"m": "рос", "f": "росла", "n": "росло", "p": "росли"}[g]
    return {"m": "снижался", "f": "снижалась", "n": "снижалось", "p": "снижались"}[g]


def _turnaround_verb(positive: bool, metric_ru: str) -> str:
    from deckdna.analysis.values import head_gender

    g = head_gender(metric_ru)
    if positive:
        return {"m": "вышел", "f": "вышла", "n": "вышло", "p": "вышли"}[g]
    return {"m": "ушёл", "f": "ушла", "n": "ушло", "p": "ушли"}[g]


def allowed_title_ids_for_fact(fact: Fact) -> list[str]:
    """Определить допустимые шаблоны заголовков по типу, предикату и базе факта."""
    kind = (fact.kind or "").strip().casefold()
    pred = (fact.predicate or "").strip().casefold()
    vals = fact.values or {}
    derived = fact.derived or {}

    v_from = vals.get("from", vals.get("start"))
    v_to = vals.get("to", vals.get("end"))

    if kind == "strict_trend" or pred in (
        "every_month_increase",
        "every_period_increase",
        "strict_increase",
    ):
        return ["every_month_increase", "steady_increase", "increase_pct", "increase_abs"]
    if pred in ("every_month_decrease", "every_period_decrease", "strict_decrease"):
        return ["every_month_decrease", "steady_decrease", "decrease_pct", "decrease_abs"]

    if kind == "steady_trend" or pred in ("steady_increase", "sustained_growth"):
        return ["steady_increase", "increase_pct", "increase_abs"]
    if pred in ("steady_decrease", "sustained_decline"):
        return ["steady_decrease", "decrease_pct", "decrease_abs"]

    if kind in ("endpoint_change", "trend", "change"):
        if v_from is not None and v_to is not None:
            f_val, t_val = float(v_from), float(v_to)
            if f_val == 0:
                if t_val > 0:
                    return ["zero_base_increase_abs", "increase_abs"]
                if t_val < 0:
                    return ["zero_base_decrease_abs", "decrease_abs"]
                return ["flat_span"]
            if f_val < 0:
                if f_val < t_val < 0:
                    return ["loss_reduced"]
                if t_val < f_val < 0:
                    return ["loss_deepened"]
                if t_val >= 0:
                    return ["turnaround_to_positive"]
                return ["flat_span"]
            if f_val > 0 and t_val < 0:
                return ["turnaround_to_negative"]
            if t_val > f_val:
                ratio = t_val / f_val
                out = ["increase_pct", "increase_abs"]
                if ratio >= 1.5:
                    out.insert(0, "increase_ratio")
                return out
            if t_val < f_val:
                ratio = f_val / t_val if t_val > 0 else 0.0
                out = ["decrease_pct", "decrease_abs", "no_growth_contrast"]
                if ratio >= 1.5:
                    out.insert(0, "decrease_ratio")
                return out
            return ["flat_span"]
        if pred in ("increase", "growth"):
            return ["increase_pct", "increase_abs"]
        if pred in ("decrease", "decline"):
            return ["decrease_pct", "decrease_abs", "no_growth_contrast"]
        if pred == "loss_reduced":
            return ["loss_reduced"]
        if pred == "loss_deepened":
            return ["loss_deepened"]
        if pred == "turnaround_to_positive":
            return ["turnaround_to_positive"]
        if pred == "turnaround_to_negative":
            return ["turnaround_to_negative"]
        return ["flat_span"]

    if kind == "peak" or pred == "peak":
        return ["peak_value"]
    if kind == "acceleration" or pred == "acceleration":
        return ["acceleration"]
    if kind == "deceleration" or pred == "deceleration":
        return ["deceleration"]

    if kind == "bridge" or pred == "bridge_change":
        return ["bridge_change", "increase_abs" if derived.get("delta", 0) >= 0 else "decrease_abs"]
    if kind == "driver" or pred == "driver":
        return ["driver_contribution"]

    if kind in ("share", "composition", "part_to_whole"):
        if pred in ("even", "uniform", "no_leader"):
            return ["even_distribution"]
        if pred == "concentration":
            return ["top3_concentration"]
        if pred == "tail":
            return ["tail_share"]
        if pred == "dominant_share" or fact.unit == "%":
            return ["share_dominant", "share_leader"]
        return ["share_leader", "share_dominant"]

    if kind == "comparison":
        if pred == "gap":
            return ["leader_gap"]
        if pred in ("even", "uniform", "no_leader"):
            return ["even_distribution"]
        return ["share_leader", "leader_gap"]

    if kind == "concentration" or pred == "concentration":
        return ["top3_concentration"]

    if kind in ("dispersion", "distribution", "spread"):
        v_min = vals.get("min")
        v_max = vals.get("max")
        out_ids = ["dispersion_range", "dispersion_median_iqr"]
        if v_min is not None and v_max is not None and float(v_min) > 0:
            if float(v_max) / float(v_min) >= 2.0:
                out_ids.insert(0, "dispersion_ratio")
        return out_ids

    if kind == "outlier" or pred == "outlier":
        return ["outlier_count"]

    if kind in ("correlation", "relationship"):
        if pred == "no_correlation":
            return ["no_correlation_r"]
        return ["correlation_r"]

    if kind == "cross_table" or pred in ("cross_table_sync", "cross_table_leader", "cross_table"):
        if "leader" in vals or pred == "cross_table_leader":
            return ["cross_table_leader"]
        return ["cross_table_sync"]

    if kind == "kpi" or pred == "kpi":
        return ["kpi_value"]
    if kind == "reference" or pred == "reference":
        return ["reference_top_n"]
    if kind == "missing" or pred == "missing":
        return ["missing_share"]

    return ["generic_fact"]


def render_title(realization_id: str, fact: Fact) -> str:
    """Сформировать заголовок слайда/вывода по ID шаблона и объекту ``Fact``.

    Проверяет, что ``realization_id`` разрешён в ``fact.allowed_title_ids``
    и совместим с базой и предикатом факта."""
    if fact.allowed_title_ids and realization_id not in fact.allowed_title_ids:
        raise ValueError(
            f"Шаблон {realization_id!r} не входит в allowed_title_ids={fact.allowed_title_ids} "
            f"для факта {fact.id!r}"
        )

    vals = fact.values or {}
    derived = fact.derived or {}
    comp = fact.comparison or {}
    unit = format_unit_ru(fact.unit)
    metric_ru = format_metric_title_ru(fact.metric)
    metric_gen = format_metric_genitive_ru(fact.metric)
    span_ru = format_period_span_ru(comp)

    v_from = vals.get("from", vals.get("start"))
    v_to = vals.get("to", vals.get("end"))
    delta = derived.get("delta")
    if delta is None and v_from is not None and v_to is not None:
        delta = float(v_to) - float(v_from)
    pct = derived.get("pct")
    if pct is None and v_from is not None and v_to is not None and float(v_from) > 0:
        pct = (float(v_to) - float(v_from)) / float(v_from) * 100.0

    # Проверка базы для процентных и кратных шаблонов
    if realization_id in ("increase_pct", "decrease_pct", "increase_ratio", "decrease_ratio"):
        if v_from is not None and float(v_from) <= 0:
            raise ValueError(
                f"Шаблон {realization_id!r} запрещён при неположительной базе from={v_from}"
            )
    if realization_id in ("loss_reduced", "loss_deepened", "turnaround_to_positive"):
        if v_from is not None and float(v_from) >= 0:
            raise ValueError(
                f"Шаблон {realization_id!r} требует отрицательную базу (from < 0), "
                f"получено {v_from}"
            )

    if realization_id == "increase_pct":
        if pct is None:
            raise ValueError(f"Для шаблона increase_pct требуется derived['pct'] в факте {fact.id}")
        pct_txt = format_pct_ru(abs(float(pct)))
        if v_from is not None and v_to is not None:
            from_txt = format_value_ru(float(v_from), None)
            to_txt = format_value_ru(float(v_to), unit)
            prep = _prep_s(from_txt)
            return (
                f"{metric_ru} {verb('grow', metric_ru)} на {pct_txt} {span_ru}: "
                f"{prep} {from_txt} до {to_txt}"
            )
        return f"{metric_ru} {verb('grow', metric_ru)} на {pct_txt} {span_ru}"

    if realization_id == "decrease_pct":
        if pct is None:
            raise ValueError(f"Для шаблона decrease_pct требуется derived['pct'] в факте {fact.id}")
        pct_txt = format_pct_ru(abs(float(pct)))
        if v_from is not None and v_to is not None:
            from_txt = format_value_ru(float(v_from), None)
            to_txt = format_value_ru(float(v_to), unit)
            prep = _prep_s(from_txt)
            return (
                f"{metric_ru} {verb('fall', metric_ru)} на {pct_txt} {span_ru}: "
                f"{prep} {from_txt} до {to_txt}"
            )
        return f"{metric_ru} {verb('fall', metric_ru)} на {pct_txt} {span_ru}"

    if realization_id == "increase_abs":
        if delta is None:
            raise ValueError(f"Для шаблона increase_abs требуется delta в факте {fact.id}")
        delta_txt = format_value_ru(abs(float(delta)), unit)
        if v_from is not None and v_to is not None:
            from_txt = format_value_ru(float(v_from), None)
            to_txt = format_value_ru(float(v_to), unit)
            prep = _prep_s(from_txt)
            return (
                f"{metric_ru} {verb('grow', metric_ru)} на {delta_txt} {span_ru}: "
                f"{prep} {from_txt} до {to_txt}"
            )
        return f"{metric_ru} {verb('grow', metric_ru)} на {delta_txt} {span_ru}"

    if realization_id == "decrease_abs":
        if delta is None:
            raise ValueError(f"Для шаблона decrease_abs требуется delta в факте {fact.id}")
        delta_txt = format_value_ru(abs(float(delta)), unit)
        if v_from is not None and v_to is not None:
            from_txt = format_value_ru(float(v_from), None)
            to_txt = format_value_ru(float(v_to), unit)
            prep = _prep_s(from_txt)
            return (
                f"{metric_ru} {verb('fall', metric_ru)} на {delta_txt} {span_ru}: "
                f"{prep} {from_txt} до {to_txt}"
            )
        return f"{metric_ru} {verb('fall', metric_ru)} на {delta_txt} {span_ru}"

    if realization_id == "increase_ratio":
        ratio = derived.get("ratio")
        if ratio is None and v_from and v_to and float(v_from) > 0:
            ratio = float(v_to) / float(v_from)
        if ratio is None:
            raise ValueError(f"Для шаблона increase_ratio требуется ratio в факте {fact.id}")
        phrase = times_phrase(float(ratio)).replace("\u00a0", " ")
        if v_from is not None and v_to is not None:
            from_txt = format_value_ru(float(v_from), None)
            to_txt = format_value_ru(float(v_to), unit)
            prep = _prep_s(from_txt)
            return (
                f"{metric_ru} {verb('grow', metric_ru)} {phrase} {span_ru}: "
                f"{prep} {from_txt} до {to_txt}"
            )
        return f"{metric_ru} {verb('grow', metric_ru)} {phrase} {span_ru}"

    if realization_id == "decrease_ratio":
        ratio = derived.get("ratio")
        if ratio is None and v_from and v_to and float(v_to) > 0:
            ratio = float(v_from) / float(v_to)
        if ratio is None:
            raise ValueError(f"Для шаблона decrease_ratio требуется ratio в факте {fact.id}")
        phrase = times_phrase(float(ratio)).replace("\u00a0", " ")
        if v_from is not None and v_to is not None:
            from_txt = format_value_ru(float(v_from), None)
            to_txt = format_value_ru(float(v_to), unit)
            prep = _prep_s(from_txt)
            return (
                f"{metric_ru} {verb('fall', metric_ru)} {phrase} {span_ru}: "
                f"{prep} {from_txt} до {to_txt}"
            )
        return f"{metric_ru} {verb('fall', metric_ru)} {phrase} {span_ru}"

    if realization_id == "zero_base_increase_abs":
        delta_val = float(delta if delta is not None else (v_to or 0))
        to_txt = format_value_ru(float(v_to if v_to is not None else delta_val), unit)
        delta_txt = format_value_ru(abs(delta_val), unit)
        return f"{metric_ru}: рост с нуля на {delta_txt} {span_ru} (до {to_txt})"

    if realization_id == "zero_base_decrease_abs":
        delta_val = float(delta if delta is not None else (v_to or 0))
        to_txt = format_value_ru(float(v_to if v_to is not None else delta_val), unit)
        delta_txt = format_value_ru(abs(delta_val), unit)
        return f"{metric_ru}: снижение с нуля на {delta_txt} {span_ru} (до {to_txt})"

    if realization_id == "loss_reduced":
        delta_txt = format_value_ru(abs(float(delta or 0)), unit)
        from_txt = format_value_ru(float(v_from or 0), None)
        to_txt = format_value_ru(float(v_to or 0), unit)
        subj = "Убыток" if "убыт" in metric_ru.casefold() else f"Убыток ({metric_ru.casefold()})"
        return f"{subj} сократился на {delta_txt} {span_ru}: с {from_txt} до {to_txt}"

    if realization_id == "loss_deepened":
        delta_txt = format_value_ru(abs(float(delta or 0)), unit)
        from_txt = format_value_ru(float(v_from or 0), None)
        to_txt = format_value_ru(float(v_to or 0), unit)
        subj = "Убыток" if "убыт" in metric_ru.casefold() else f"Убыток ({metric_ru.casefold()})"
        return f"{subj} увеличился на {delta_txt} {span_ru}: с {from_txt} до {to_txt}"

    if realization_id == "turnaround_to_positive":
        from_txt = format_value_ru(float(v_from or 0), None)
        to_txt = format_value_ru(float(v_to or 0), unit)
        v_word = _turnaround_verb(True, metric_ru)
        return f"{metric_ru} {v_word} в плюс {span_ru}: с {from_txt} до {to_txt}"

    if realization_id == "turnaround_to_negative":
        from_txt = format_value_ru(float(v_from or 0), None)
        to_txt = format_value_ru(float(v_to or 0), unit)
        v_word = _turnaround_verb(False, metric_ru)
        prep = _prep_s(from_txt)
        return f"{metric_ru} {v_word} в минус {span_ru}: {prep} {from_txt} до {to_txt}"

    if realization_id == "no_growth_contrast":
        pct_txt = format_pct_ru(abs(float(pct or 0)))
        p_from = parse_period(_strip_period_Annotations(str(comp.get("from", ""))))
        p_to = parse_period(_strip_period_Annotations(str(comp.get("to", ""))))
        if p_from and p_to and p_from.grain == "month":
            m_to = _MONTH_NOMINATIVE_RU.get(p_to.key[1], p_to.label)
            m_from = _MONTH_GENITIVE_RU.get(p_from.key[1], p_from.label)
            return f"{metric_ru} не {verb('grow', metric_ru)}: {m_to} на {pct_txt} ниже {m_from}"
        return f"{metric_ru} не {verb('grow', metric_ru)} {span_ru}: снижение на {pct_txt}"

    if realization_id == "flat_span":
        if v_from is not None and v_to is not None:
            from_txt = format_value_ru(float(v_from), None)
            to_txt = format_value_ru(float(v_to), unit)
            return (
                f"{metric_ru} {verb('flat', metric_ru)} без заметного тренда {span_ru}: "
                f"{from_txt}–{to_txt}"
            )
        return f"{metric_ru} {verb('flat', metric_ru)} без заметного тренда {span_ru}"

    if realization_id == "steady_increase":
        v_word = _ongoing_verb("grow", metric_ru)
        pct_txt = format_pct_ru(abs(float(pct or 0)), signed=True)
        to_txt = format_value_ru(float(v_to or 0), unit)
        return f"{metric_ru} устойчиво {v_word} {span_ru}: {pct_txt} (до {to_txt})"

    if realization_id == "steady_decrease":
        v_word = _ongoing_verb("fall", metric_ru)
        pct_txt = format_pct_ru(-abs(float(pct or 0)), signed=True)
        to_txt = format_value_ru(float(v_to or 0), unit)
        return f"{metric_ru} устойчиво {v_word} {span_ru}: {pct_txt} (до {to_txt})"

    if realization_id in ("every_month_increase", "every_period_increase"):
        v_word = _ongoing_verb("grow", metric_ru)
        pct_txt = format_pct_ru(abs(float(pct or 0)), signed=True)
        to_txt = format_value_ru(float(v_to or 0), unit)
        return f"{metric_ru} {v_word} каждый месяц {span_ru}: {pct_txt} (до {to_txt})"

    if realization_id in ("every_month_decrease", "every_period_decrease"):
        v_word = _ongoing_verb("fall", metric_ru)
        pct_txt = format_pct_ru(-abs(float(pct or 0)), signed=True)
        to_txt = format_value_ru(float(v_to or 0), unit)
        return f"{metric_ru} {v_word} каждый месяц {span_ru}: {pct_txt} (до {to_txt})"

    if realization_id == "peak_value":
        peak_val = vals.get("peak", vals.get("value", v_to or 0))
        peak_period = comp.get("period") or comp.get("peak_period") or ""
        val_txt = format_value_ru(float(peak_val), unit)
        return f"{metric_ru} {verb('peak', metric_ru)} максимума в {peak_period}: {val_txt}".strip()

    if realization_id == "acceleration":
        g1 = derived.get("g1_pct", vals.get("g1_pct", 0.0))
        g2 = derived.get("g2_pct", vals.get("g2_pct", 0.0))
        return (
            f"Рост {metric_gen} ускорился во второй половине периода: "
            f"{format_pct_ru(g2, signed=True)} против {format_pct_ru(g1, signed=True)}"
        )

    if realization_id == "deceleration":
        g1 = derived.get("g1_pct", vals.get("g1_pct", 0.0))
        g2 = derived.get("g2_pct", vals.get("g2_pct", 0.0))
        return (
            f"Рост {metric_gen} замедлился во второй половине периода: "
            f"{format_pct_ru(g2, signed=True)} против {format_pct_ru(g1, signed=True)}"
        )

    if realization_id == "bridge_change":
        to_txt = format_value_ru(float(v_to or 0), unit)
        driver = vals.get("driver") or comp.get("driver")
        v_word = verb("grow" if float(delta or 0) >= 0 else "fall", metric_ru)
        suffix = f" — главный фактор: {driver}" if driver else ""
        return f"{metric_ru} {v_word} до {to_txt}{suffix}"

    if realization_id == "driver_contribution":
        driver = (
            vals.get("driver") or comp.get("driver") or next(iter(fact.scope.values()), "сегмент")
        )
        contrib = vals.get("contribution", delta if delta is not None else vals.get("value", 0))
        c_txt = format_value_ru(float(contrib), unit)
        if float(contrib) > 0 and not c_txt.startswith("+"):
            c_txt = f"+{c_txt}"
        return f"На сегмент «{driver}» приходится {c_txt} изменения {metric_gen}"

    if realization_id in ("share_leader", "leader_share"):
        leader = vals.get("leader") or next(iter(fact.scope.values()), "Лидер")
        sh = derived.get("share_pct", derived.get("pct", vals.get("share_pct", 0)))
        part = vals.get("part", vals.get("top", vals.get("value")))
        sh_txt = format_pct_ru(float(sh))
        if part is not None and unit and unit != "%":
            return f"{leader} — {sh_txt} {metric_gen} ({format_value_ru(float(part), unit)})"
        return f"{leader} — {sh_txt} {metric_gen}"

    if realization_id == "share_dominant":
        leader = vals.get("leader") or next(iter(fact.scope.values()), "Лидер")
        sh = derived.get(
            "share_pct", derived.get("pct", vals.get("share_pct", vals.get("part", 0)))
        )
        return f"{leader} — крупнейшая доля, {format_pct_ru(float(sh))}"

    if realization_id == "leader_gap":
        leader = vals.get("leader") or "Лидер"
        runner_up = vals.get("runner_up") or comp.get("baseline") or "второе место"
        gap = derived.get("gap_ratio", derived.get("ratio", 1.5))
        return f"{leader} {times_phrase(float(gap)).replace('\u00a0', ' ')} больше, чем {runner_up}"

    if realization_id in ("top3_concentration", "concentration_top3"):
        dim = vals.get("dimension") or next(iter(fact.scope.keys()), "категориям")
        sh = derived.get("top3_pct", derived.get("share_pct", derived.get("pct", 0)))
        return f"Топ-3 по «{dim}» — {format_pct_ru(float(sh))} {metric_gen}"

    if realization_id in ("even_distribution", "uniform_share"):
        dim = vals.get("dimension") or next(iter(fact.scope.keys()), "категориям")
        sh = derived.get("max_share_pct", derived.get("share_pct", vals.get("max_share_pct", 0)))
        return f"По разрезу «{dim}» нет явного лидера (максимум {format_pct_ru(float(sh))})"

    if realization_id == "tail_share":
        tail = vals.get("tail") or vals.get("category") or "Минимум"
        sh = derived.get("share_pct", derived.get("pct", vals.get("value", 0)))
        return f"{tail}: наименьшая доля, {format_pct_ru(float(sh))}"

    if realization_id == "dispersion_ratio":
        v_min = float(vals["min"])
        v_max = float(vals["max"])
        ratio = derived.get("ratio", v_max / v_min if v_min > 0 else 1.0)
        phrase = times_phrase(float(ratio)).replace("\u00a0", " ")
        min_txt = format_value_ru(v_min, None)
        max_txt = format_value_ru(v_max, unit)
        return f"{metric_ru}: значения различаются {phrase}, от {min_txt} до {max_txt}"

    if realization_id == "dispersion_range":
        v_min = float(vals["min"])
        v_max = float(vals["max"])
        med = vals.get("median")
        min_txt = format_value_ru(v_min, None)
        max_txt = format_value_ru(v_max, unit)
        if med is not None:
            med_txt = format_value_ru(float(med), unit)
            return f"{metric_ru}: медиана {med_txt}, разброс от {min_txt} до {max_txt}"
        return f"{metric_ru}: значения от {min_txt} до {max_txt}"

    if realization_id == "dispersion_median_iqr":
        med = float(vals["median"])
        q1 = vals.get("q1")
        q3 = vals.get("q3")
        iqr = derived.get("iqr")
        med_txt = format_value_ru(med, unit)
        if q1 is not None and q3 is not None:
            q1_txt = format_value_ru(float(q1), None)
            q3_txt = format_value_ru(float(q3), unit)
            return f"{metric_ru}: медиана {med_txt}, межквартильный размах от {q1_txt} до {q3_txt}"
        iqr_txt = format_value_ru(float(iqr or 0), unit)
        return f"{metric_ru}: медиана {med_txt}, IQR {iqr_txt}"

    if realization_id == "outlier_count":
        cnt = int(vals.get("outlier_count", vals.get("count", 1)))
        max_v = float(vals.get("max", vals.get("value", 0)))
        word = plural(cnt, ("выброс", "выброса", "выбросов"))
        return f"{metric_ru}: {cnt} {word}, максимум {format_value_ru(max_v, unit)}"

    if realization_id == "correlation_r":
        r = float(derived.get("r", vals.get("r", 0.0)))
        n = int(vals.get("n", 0))
        x_m = format_metric_title_ru(str(comp.get("x_metric") or vals.get("x_metric") or "X"))
        y_m = format_metric_title_ru(
            str(comp.get("y_metric") or vals.get("y_metric") or fact.metric)
        )
        y_low = y_m if y_m.isupper() else y_m.casefold()
        r_txt = fmt_number(r, decimals=2).replace("\u00a0", " ")
        dir_w = "" if r > 0 else " обратно"
        n_part = f" (n = {n})" if n else ""
        return f"{x_m} и {y_low} связаны{dir_w}: r = {r_txt}{n_part}"

    if realization_id == "no_correlation_r":
        r = float(derived.get("r", vals.get("r", 0.0)))
        n = int(vals.get("n", 0))
        x_m = format_metric_title_ru(str(comp.get("x_metric") or vals.get("x_metric") or "X"))
        y_m = format_metric_title_ru(
            str(comp.get("y_metric") or vals.get("y_metric") or fact.metric)
        )
        r_txt = fmt_number(r, decimals=2).replace("\u00a0", " ")
        n_part = f" (n = {n})" if n else ""
        return f"Связь «{x_m}» и «{y_m}» не видна: r = {r_txt}{n_part}"

    if realization_id == "cross_table_sync":
        r = float(derived.get("r", vals.get("r", 0.0)))
        n = int(vals.get("n", 0))
        x_m = format_metric_title_ru(str(comp.get("x_metric") or vals.get("x_metric") or "X"))
        y_m = format_metric_title_ru(
            str(comp.get("y_metric") or vals.get("y_metric") or fact.metric)
        )
        y_low = y_m if y_m.isupper() else y_m.casefold()
        r_txt = fmt_number(r, decimals=2).replace("\u00a0", " ")
        direction = "синхронно" if r > 0 else "в противофазе"
        p_word = plural(n, ("период", "периода", "периодов"))
        return f"{x_m} и {y_low} движутся {direction}: r = {r_txt} за {n} {p_word}"

    if realization_id == "cross_table_leader":
        leader = vals.get("leader", "Лидер")
        sh_a = format_pct_ru(float(vals.get("share_a", 0)))
        sh_b = format_pct_ru(float(vals.get("share_b", 0)))
        return f"{leader} лидирует в обоих срезах: {sh_a} и {sh_b}"

    if realization_id == "kpi_value":
        lbl = vals.get("label") or metric_ru
        val = float(vals.get("value", 0))
        return f"{lbl}: {format_value_ru(val, unit)}"

    if realization_id == "reference_top_n":
        title = vals.get("title") or metric_ru
        top_n = int(vals.get("top_n", 6))
        total_rows = int(vals.get("total_rows", vals.get("n", 0)))
        return f"{title}: в таблице топ-{top_n} из {total_rows} позиций"

    if realization_id == "missing_share":
        col_name = vals.get("column") or metric_ru
        miss_pct = format_pct_ru(float(vals.get("missing_pct", derived.get("pct", 0))))
        return f"Пропуски: «{col_name}» не заполнено в {miss_pct} строк"

    if realization_id == "generic_fact":
        if "value" in vals:
            return f"{metric_ru}: {format_value_ru(float(vals['value']), unit)}"
        return metric_ru

    raise ValueError(f"Неизвестный шаблон заголовка: {realization_id!r}")


def default_title_for_fact(fact: Fact) -> str:
    """Рендер основного заголовка факта (первый из ``allowed_title_ids``)."""
    ids = fact.allowed_title_ids or allowed_title_ids_for_fact(fact)
    return render_title(ids[0], fact)


__all__ = [
    "allowed_title_ids_for_fact",
    "default_title_for_fact",
    "format_metric_genitive_ru",
    "format_metric_title_ru",
    "format_pct_ru",
    "format_period_span_ru",
    "format_unit_ru",
    "format_value_ru",
    "render_title",
]
