"""Выводы «факт → что значит → что следует» (ADR-033 §5), шаг 1: факты.

Факты и заголовок-вывод считает код по шаблонам фраз задачи. Каждый
факт структурирован: метрика, значения, период, единица, ссылки на
ячейки; ``numbers`` — числа как в тексте, по ним сверяются формулировки
модели (шаги 2–3, ``analysis/interpret.py``). Без модели ``meaning`` и
``implication`` пусты: следствия не выдумываются."""

from __future__ import annotations

import math
import re
import statistics

from deckdna.analysis.profile import PERCENT, TableProfile
from deckdna.analysis.values import (
    fmt_number,
    fmt_ratio,
    fmt_scaled,
    fmt_signed_pct,
    plural,
    times_phrase,
    verb,
)
from deckdna.analysis.views import (
    CATEGORICAL,
    DISTRIBUTION,
    KPI,
    REFERENCE,
    REFERENCE_TOP,
    RELATIONSHIP,
    SERIES,
    DataView,
)
from deckdna.contracts.dataset import Insight

# родительный падеж частых метрик: «41% выручки»
_GENITIVE = {
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
    "revenue": "revenue",
    "sales": "sales",
}
_PERIOD_WORD = {
    "кварталам": ("квартал", "квартала", "кварталов"),
    "месяцам": ("месяц", "месяца", "месяцев"),
    "неделям": ("неделя", "недели", "недель"),
    "годам": ("год", "года", "лет"),
}


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    return plural(n, forms)


def _cap(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


# «Млн ₽» — единица измерения, а не метрика: Action Title строим от
# нейтрального имени, единица остаётся в факте («с X до Y млн ₽»).
_UNIT_ONLY = re.compile(
    r"^(млн|млрд|тыс\.?|м)?\s*(₽|руб\.?|%|шт\.?|ед\.?|чел\.?|usd|eur|\$|€)\s*$",
    re.IGNORECASE,
)
_UNIT_SUFFIX = re.compile(
    r"[\s,]+\((?:млн|млрд|тыс\.?|в)\s[^)]*\)$"
    r"|[\s,]+(?:млн|млрд|тыс\.?)\s*(?:₽|руб\.?|%|шт\.?|ед\.?|чел\.?|usd|eur|\$|€)\s*$"
    r"|[\s,]+(?:₽|руб\.?|%|шт\.?|ед\.?|чел\.?)\s*$",
    re.IGNORECASE,
)


def _metric_title(name: str) -> str:
    """Имя метрики для заголовка-вывода: единица измерения в конец
    заголовка не идёт («Цена, млн ₽» → «Цена»), серии-«единицы»
    («Млн ₽») заменяются нейтральным «Показатель»."""
    name = name.strip()
    if _UNIT_ONLY.match(name):
        return "Показатель"
    clean = _UNIT_SUFFIX.sub("", name).strip(" ,")
    return _cap(clean or name)


def genitive(name: str) -> str:
    low = name.strip().casefold()
    if low in _GENITIVE:
        return _GENITIVE[low]
    return f"показателя «{name}»"


def _val(value: float, unit: str | None) -> str:
    return fmt_scaled(value, unit)


def _first_last(values: list[float | None]) -> tuple[int, int] | None:
    idx = [i for i, v in enumerate(values) if v is not None]
    return (idx[0], idx[-1]) if len(idx) >= 2 else None


def _grain_key(view: DataView) -> str:
    cats = view.categories
    if cats and "кв." in cats[0]:
        return "кварталам"
    if cats and cats[0].startswith("нед."):
        return "неделям"
    if cats and cats[0].isdigit():
        return "годам"
    return "месяцам"


def _span_fact(view: DataView, s, suffix: str) -> list[Insight]:
    """Факт «от начала к концу» для одного ряда. В мультирядном
    представлении у каждого ряда — свой факт: «ушедшие клиенты» не
    должны теряться за «новыми»."""
    span = _first_last(s.values)
    if span is None:
        return []
    i, j = span
    a, b = s.values[i], s.values[j]
    unit = s.unit
    periods = j - i + 1
    word = _plural(periods, _PERIOD_WORD[_grain_key(view)])
    if not (a and a > 0):
        return []
    ratio = b / a
    pct = (ratio - 1) * 100
    title = _metric_title(s.name)
    if ratio >= 1.5:
        headline = f"{title} {verb('grow', s.name)} {times_phrase(ratio)} за {periods} {word}"
        fact = (
            f"{s.name}: с {_val(a, unit)} ({view.categories[i]}) до {_val(b, unit)} "
            f"({view.categories[j]}), рост {times_phrase(ratio)}"
        )
        nums = [fmt_ratio(ratio), _val(a, unit), _val(b, unit)]
        ftype, strength = "growth", min(1.0, 0.6 + (ratio - 1) / 4)
    elif pct >= 5:
        headline = (
            f"{title} {verb('grow', s.name)} на {fmt_number(pct, decimals=0)}% за {periods} {word}"
        )
        fact = (
            f"{s.name}: с {_val(a, unit)} ({view.categories[i]}) до {_val(b, unit)} "
            f"({view.categories[j]}), +{fmt_number(pct, decimals=0)}%"
        )
        nums = [fmt_number(pct, decimals=0), _val(a, unit), _val(b, unit)]
        ftype, strength = "growth", min(0.9, 0.45 + pct / 200)
    elif pct <= -5:
        headline = (
            f"{title} {verb('fall', s.name)} на {fmt_number(-pct, decimals=0)}% за {periods} {word}"
        )
        fact = (
            f"{s.name}: с {_val(a, unit)} ({view.categories[i]}) до {_val(b, unit)} "
            f"({view.categories[j]}), −{fmt_number(-pct, decimals=0)}%"
        )
        nums = [fmt_number(-pct, decimals=0), _val(a, unit), _val(b, unit)]
        ftype, strength = "decline", min(0.95, 0.5 + -pct / 150)
    else:
        headline = f"{title} {verb('flat', s.name)} без заметного тренда за {periods} {word}"
        fact = (
            f"{s.name}: {_val(a, unit)} в начале и {_val(b, unit)} в конце периода, "
            f"изменение {fmt_number(pct, decimals=1)}%"
        )
        nums = [_val(a, unit), _val(b, unit)]
        ftype, strength = "flat", 0.35
    return [
        Insight(
            id=f"{view.id}{suffix}-i0",
            dataset_id=view.dataset_id,
            view_id=view.id,
            fact_type=ftype,
            fact=fact,
            headline=headline,
            strength=round(strength, 3),
            evidence=list(view.evidence),
            numbers=nums,
        )
    ]


def _series_facts(view: DataView) -> list[Insight]:
    out: list[Insight] = []
    s = view.metric
    # факт «от начала к концу» — по каждому ряду; заголовком слайда
    # становится самый сильный ряд, а не обязательно первый
    for si, series in enumerate(view.series[:5]):
        out.extend(_span_fact(view, series, f"-s{si}" if si else ""))
    span = _first_last(s.values)
    if span is None:
        return out
    i, j = span
    a = s.values[i]
    unit = s.unit
    ev = list(view.evidence)
    # пик
    known = [(k, v) for k, v in enumerate(s.values) if v is not None]
    if len(known) >= 4:
        k, v = max(known, key=lambda kv: kv[1])
        if k != j:
            out.append(
                Insight(
                    id=f"{view.id}-i1",
                    dataset_id=view.dataset_id,
                    view_id=view.id,
                    fact_type="peak",
                    fact=f"Пик: {view.categories[k]}, {_val(v, unit)}",
                    headline=(
                        f"{_metric_title(s.name)} {verb('peak', s.name)} максимума "
                        f"в {view.categories[k]}: {_val(v, unit)}"
                    ),
                    strength=0.45,
                    evidence=ev,
                    numbers=[_val(v, unit)],
                )
            )
    # ускорение: вторая половина против первой
    if len(known) >= 6 and a and a > 0:
        mid = len(known) // 2
        first = known[:mid]
        second = known[mid:]
        if first[0][1] > 0 and second[0][1] > 0:
            g1 = (first[-1][1] / first[0][1] - 1) * 100
            g2 = (second[-1][1] / second[0][1] - 1) * 100
            if g2 - g1 >= 10 and g2 > 0:
                out.append(
                    Insight(
                        id=f"{view.id}-i2",
                        dataset_id=view.dataset_id,
                        view_id=view.id,
                        fact_type="acceleration",
                        fact=(
                            f"Рост ускорился во второй половине периода: "
                            f"{fmt_signed_pct(g2)} против {fmt_signed_pct(g1)}"
                        ),
                        headline=(f"Рост {genitive(s.name)} ускорился во второй половине периода"),
                        strength=0.55,
                        evidence=ev,
                        numbers=[fmt_number(g2, decimals=0), fmt_number(g1, decimals=0)],
                    )
                )
            elif g1 - g2 >= 10:
                out.append(
                    Insight(
                        id=f"{view.id}-i2",
                        dataset_id=view.dataset_id,
                        view_id=view.id,
                        fact_type="deceleration",
                        fact=(
                            f"Рост замедлился во второй половине периода: "
                            f"{fmt_signed_pct(g2)} против {fmt_signed_pct(g1)}"
                        ),
                        headline=(f"Рост {genitive(s.name)} замедлился во второй половине периода"),
                        strength=0.5,
                        evidence=ev,
                        numbers=[fmt_number(g2, decimals=0), fmt_number(g1, decimals=0)],
                    )
                )
    return out


def _bridge_facts(view: DataView) -> list[Insight]:
    """Мост отклонений: «с A до B» и главный фактор вместо долей —
    проценты от суммы моста не имеют смысла."""
    s = view.metric
    pairs = [(c, v) for c, v in zip(view.categories, s.values, strict=False) if v is not None]
    if len(pairs) < 3:
        return []
    (start_cat, start), (end_cat, end) = pairs[0], pairs[-1]
    mids = pairs[1:-1]
    delta = end - start
    unit = s.unit
    top_mid = max(mids, key=lambda kv: abs(kv[1]), default=None)
    sign = "рост" if delta >= 0 else "снижение"
    out = [
        Insight(
            id=f"{view.id}-i0",
            dataset_id=view.dataset_id,
            view_id=view.id,
            fact_type="growth" if delta >= 0 else "decline",
            fact=(
                f"{s.name}: с {_val(start, unit)} ({start_cat}) до {_val(end, unit)} "
                f"({end_cat}), {sign} {_val(delta, unit)} за счёт "
                f"{len(mids)} {plural(len(mids), ('фактора', 'факторов', 'факторов'))}"
            ),
            headline=(
                f"{_metric_title(s.name)} {verb('grow' if delta >= 0 else 'fall', s.name)} "
                f"до {_val(end, unit)}" + (f" — драйвер: {top_mid[0]}" if top_mid else "")
            ),
            strength=0.7,
            evidence=list(view.evidence),
            numbers=[_val(start, unit), _val(end, unit), _val(delta, unit), str(len(mids))],
        )
    ]
    if top_mid:
        direction = "увеличил итог" if top_mid[1] >= 0 else "уменьшил итог"
        out.append(
            Insight(
                id=f"{view.id}-i1",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="driver",
                fact=f"{top_mid[0]}: {direction} на {_val(abs(top_mid[1]), unit)}",
                headline=f"Главный фактор: {top_mid[0]} ({_val(top_mid[1], unit)})",
                strength=0.55,
                evidence=list(view.evidence),
                numbers=[_val(abs(top_mid[1]), unit)],
            )
        )
    return out


def _is_flat_categorical(pairs: list[tuple[str, float]], top_share_pct: float) -> bool:
    """Проверить, действительно ли в распределении по категориям нет лидера."""
    n = len(pairs)
    if n < 4 or top_share_pct >= 25.0:
        return False
    uniform_share = 100.0 / n
    top_val = pairs[0][1]
    second_val = pairs[1][1] if len(pairs) >= 2 else 0.0
    # Если первая категория как минимум вдвое выше равномерной доли и заметно
    # опережает вторую (>= 1.25x), это настоящий лидер (например, Москва 20% из 14 регионов).
    if top_share_pct >= 2.0 * uniform_share and second_val > 0 and top_val >= second_val * 1.25:
        return False
    return True


_BARE_LABEL = re.compile(r"^\s*([\d\s.,–\-+<>]+|\S{1,2})\s*$")


def _bare(label: str, bare: bool) -> str:
    return f"«{label}»" if bare else label


def _categorical_facts(view: DataView) -> list[Insight]:
    s = view.metric
    pairs = [(c, v) for c, v in zip(view.categories, s.values, strict=False) if v is not None]
    total = sum(v for _, v in pairs)
    if not pairs or total <= 0:
        return []
    pairs.sort(key=lambda kv: -kv[1])
    leader, top = pairs[0]
    # почти равные доли/значения — лидера нет; проверка идёт до ветки
    # процентов, иначе «16,7% × 6» читалось бы как «крупнейшая доля»
    if len(pairs) >= 2 and pairs[-1][1] >= top * 0.9:
        n = len(pairs)
        if s.kind == PERCENT and 97 <= total <= 103:
            each = fmt_number(top)
            return [
                Insight(
                    id=f"{view.id}-i0",
                    dataset_id=view.dataset_id,
                    view_id=view.id,
                    fact_type="even",
                    fact=(
                        f"Равномерное распределение по {n} "
                        f"{plural(n, ('категории', 'категориям', 'категориям'))} "
                        f"(по {each}%)"
                    ),
                    headline=f"Доли {view.category_label.casefold()} почти равны: по {each}%",
                    strength=0.3,
                    evidence=list(view.evidence),
                    numbers=[each, str(n)],
                )
            ]
        return [
            Insight(
                id=f"{view.id}-i0",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="even",
                fact=f"{view.category_label}: поровну, по {_val(top, s.unit)} в каждой из {n}",
                headline=f"{view.category_label}: поровну, по {_val(top, s.unit)}",
                strength=0.3,
                evidence=list(view.evidence),
                numbers=[_val(top, s.unit), str(n)],
            )
        ]
    if s.kind == PERCENT and 97 <= total <= 103:
        return _percent_share_facts(view, pairs)
    share = top / total * 100
    gen = genitive(s.name)
    # «0 — 82% записей» без разреза не читается: голые числа и коды
    # подписываем названием колонки
    bare = any(_BARE_LABEL.match(str(lbl)) for lbl, _ in pairs[:2])
    lead_txt = f"{view.category_label}: «{leader}»" if bare else leader
    # Лидер «9%» — не лидер: при почти равномерном распределении
    # вывод честнее — «доминирующей позиции нет».
    if _is_flat_categorical(pairs, share):
        return [
            Insight(
                id=f"{view.id}-i0",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="even",
                fact=(
                    f"Распределение по «{view.category_label}» равномерное: "
                    f"максимум {fmt_number(share, decimals=0)}% у «{leader}»"
                ),
                headline=(
                    f"По разрезу «{view.category_label}» нет явного лидера "
                    f"(максимум {fmt_number(share, decimals=0)}%)"
                ),
                strength=0.4,
                evidence=list(view.evidence),
                numbers=[fmt_number(share, decimals=0)],
            )
        ]
    out = [
        Insight(
            id=f"{view.id}-i0",
            dataset_id=view.dataset_id,
            view_id=view.id,
            fact_type="leader",
            fact=f"{lead_txt}: {fmt_number(share, decimals=0)}% {gen} ({_val(top, s.unit)})",
            headline=f"{lead_txt} — {fmt_number(share, decimals=0)}% {gen}",
            strength=round(min(1.0, max(0.68, 0.35 + share / 100)), 3),
            evidence=list(view.evidence),
            numbers=[fmt_number(share, decimals=0), _val(top, s.unit)],
        )
    ]
    if len(pairs) >= 2 and pairs[1][1] > 0:
        second, v2 = pairs[1]
        gap = top / v2
        gap_text = f"{lead_txt} {times_phrase(gap)} больше, чем {_bare(second, bare)}"
        if gap >= 1.2:
            out.append(
                Insight(
                    id=f"{view.id}-i1",
                    dataset_id=view.dataset_id,
                    view_id=view.id,
                    fact_type="gap",
                    fact=gap_text
                    if bare
                    else f"{leader}: {times_phrase(gap)} больше, чем {second}",
                    headline=gap_text,
                    strength=round(min(0.85, 0.3 + (gap - 1) / 3), 3),
                    evidence=list(view.evidence),
                    numbers=[fmt_ratio(gap)],
                )
            )
    if len(pairs) >= 5:
        top3 = sum(v for _, v in pairs[:3]) / total * 100
        out.append(
            Insight(
                id=f"{view.id}-i2",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="concentration",
                fact=(f"Топ-3 «{view.category_label}»: {fmt_number(top3, decimals=0)}% итога"),
                headline=(
                    f"Топ-3 по «{view.category_label}» — {fmt_number(top3, decimals=0)}% {gen}"
                ),
                strength=round(min(0.75, top3 / 130), 3),
                evidence=list(view.evidence),
                numbers=[fmt_number(top3, decimals=0)],
            )
        )
    return out


def _percent_share_facts(view: DataView, pairs: list[tuple[str, float]]) -> list[Insight]:
    """Доли, уже данные в процентах: значение и есть доля."""
    (leader, top), rest = pairs[0], pairs[1:]
    # «Лидер» с долей < 25% при многих категориях — не лидер.
    if _is_flat_categorical(pairs, top):
        return [
            Insight(
                id=f"{view.id}-i0",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="even",
                fact=(
                    f"Доли по «{view.category_label}» без явного лидера: "
                    f"максимум {fmt_number(top)}% у «{leader}»"
                ),
                headline=(
                    f"По разрезу «{view.category_label}» нет явного лидера "
                    f"(максимум {fmt_number(top)}%)"
                ),
                strength=0.4,
                evidence=list(view.evidence),
                numbers=[fmt_number(top)],
            )
        ]
    out = [
        Insight(
            id=f"{view.id}-i0",
            dataset_id=view.dataset_id,
            view_id=view.id,
            fact_type="leader",
            fact=f"{leader}: {fmt_number(top)}%, крупнейшая доля",
            headline=f"{leader} — крупнейшая доля, {fmt_number(top)}%",
            strength=round(min(1.0, max(0.68, 0.4 + top / 100)), 3),
            evidence=list(view.evidence),
            numbers=[fmt_number(top)],
        )
    ]
    if len(pairs) >= 4:
        top3 = sum(v for _, v in pairs[:3])
        out.append(
            Insight(
                id=f"{view.id}-i1",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="concentration",
                fact=(f"Топ-3 ({', '.join(c for c, _ in pairs[:3])}): {fmt_number(top3)}% итога"),
                headline=f"Топ-3 — {fmt_number(top3)}% итога",
                strength=round(min(0.75, top3 / 130), 3),
                evidence=list(view.evidence),
                numbers=[fmt_number(top3)],
            )
        )
    if rest:
        low, value = rest[-1]
        out.append(
            Insight(
                id=f"{view.id}-i2",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="tail",
                fact=f"{low}: {fmt_number(value)}%, наименьшая доля",
                headline=f"{low}: наименьшая доля, {fmt_number(value)}%",
                strength=0.3,
                evidence=list(view.evidence),
                numbers=[fmt_number(value)],
            )
        )
    return out


def _distribution_facts(view: DataView) -> list[Insight]:
    from deckdna.analysis.guard import is_wide_spread

    values = sorted(view.raw_values)
    if len(values) < 5:
        return []
    q1, med, q3 = statistics.quantiles(values, n=4)
    unit = view.unit
    wide = is_wide_spread(values)
    m_title = _metric_title(view.metric.name)
    if wide:
        fact = (
            f"Медиана {_val(med, unit)}, половина значений от {_val(q1, unit)} "
            f"до {_val(q3, unit)}; разброс {_val(values[0], unit)}–{_val(values[-1], unit)}"
        )
        headline = (
            f"{m_title}: медиана {_val(med, unit)}, "
            f"разброс {_val(values[0], unit)}–{_val(values[-1], unit)}"
        )
        nums = [
            _val(med, unit),
            _val(q1, unit),
            _val(q3, unit),
            _val(values[0], unit),
            _val(values[-1], unit),
        ]
        ftype = "wide_spread"
        strength = 0.72
    else:
        fact = (
            f"Медиана {_val(med, unit)}, половина значений от {_val(q1, unit)} до {_val(q3, unit)}"
        )
        headline = f"{m_title}: типичное значение {_val(med, unit)}"
        nums = [_val(med, unit), _val(q1, unit), _val(q3, unit)]
        ftype = "spread"
        strength = 0.58
    out = [
        Insight(
            id=f"{view.id}-i0",
            dataset_id=view.dataset_id,
            view_id=view.id,
            fact_type=ftype,
            fact=fact,
            headline=headline,
            strength=strength,
            evidence=list(view.evidence),
            numbers=nums,
        )
    ]
    iqr = q3 - q1
    outliers = [v for v in values if v > q3 + 1.5 * iqr or v < q1 - 1.5 * iqr]
    if outliers:
        out.append(
            Insight(
                id=f"{view.id}-i1",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="outlier",
                fact=(
                    f"Выбросы: {len(outliers)} значений заметно выбиваются, "
                    f"максимум {_val(max(values), unit)}"
                ),
                headline=f"{len(outliers)} выбросов, максимум {_val(max(values), unit)}",
                strength=0.45,
                evidence=list(view.evidence),
                numbers=[str(len(outliers)), _val(max(values), unit)],
            )
        )
    return out


def pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=False)) / (sx * sy)


def _relationship_facts(view: DataView) -> list[Insight]:
    r = pearson(view.x_values, view.metric.values)
    if r is None:
        return []
    n = len(view.x_values)
    rtxt = fmt_number(r, decimals=2)
    x, y = _metric_title(view.x_label or "X"), _metric_title(view.metric.name)
    if abs(r) >= 0.5:
        # Сильный r на малой выборке — всё равно связь, а не «связи нет»:
        # «не видна (r = 0,96)» противоречит собственному числу. Малое n
        # честно оговаривается и снижает вес факта.
        small = n < 10
        return [
            Insight(
                id=f"{view.id}-i0",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="correlation",
                fact=(
                    f"Связь «{x}» и «{y}»: r = {rtxt} по {n} наблюдениям"
                    + (" — выборка мала, вывод предварительный" if small else "")
                ),
                headline=(
                    f"{x} и {y.casefold()} связаны{'' if r > 0 else ' обратно'}: r = {rtxt}"
                    + (f" по {n} наблюдениям" if small else "")
                ),
                strength=round(min(0.95, abs(r)) * (0.7 if small else 1.0), 3),
                evidence=list(view.evidence),
                numbers=[rtxt, str(n)],
            )
        ]
    if abs(r) >= 0.3:
        return [
            Insight(
                id=f"{view.id}-i0",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="no_correlation",
                fact=f"Связь «{x}» и «{y}» слабая: r = {rtxt} по {n} наблюдениям",
                headline=f"Связь «{x}» и «{y}» слабая (r = {rtxt})",
                strength=0.3,
                evidence=list(view.evidence),
                numbers=[rtxt, str(n)],
            )
        ]
    return [
        Insight(
            id=f"{view.id}-i0",
            dataset_id=view.dataset_id,
            view_id=view.id,
            fact_type="no_correlation",
            fact=f"Заметной связи «{x}» и «{y}» нет: r = {rtxt} по {n} наблюдениям",
            headline=f"Связь «{x}» и «{y}» не видна (r = {rtxt})",
            strength=0.3,
            evidence=list(view.evidence),
            numbers=[rtxt, str(n)],
        )
    ]


def _kpi_facts(view: DataView) -> list[Insight]:
    out = []
    units = view.kpi_units or [view.unit] * len(view.categories)
    for k, (label, value) in enumerate(zip(view.categories, view.metric.values, strict=False)):
        if value is None:
            continue
        unit = units[k] if k < len(units) else None
        out.append(
            Insight(
                id=f"{view.id}-i{k}",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="kpi",
                fact=f"{label}: {_val(value, unit)}",
                headline=f"{label}: {_val(value, unit)}",
                strength=round(0.6 - 0.05 * k, 3),
                evidence=list(view.evidence),
                numbers=[_val(value, unit)],
            )
        )
    return out


def view_insights(view: DataView) -> list[Insight]:
    from deckdna.analysis.guard import guard_insight

    if view.shape == SERIES:
        raw = _series_facts(view)
    elif view.shape == CATEGORICAL:
        raw = _bridge_facts(view) if view.bridge else _categorical_facts(view)
    elif view.shape == DISTRIBUTION:
        raw = _distribution_facts(view)
    elif view.shape == RELATIONSHIP:
        raw = _relationship_facts(view)
    elif view.shape == KPI:
        raw = _kpi_facts(view)
    elif view.shape == REFERENCE and view.primary_hint:
        n = view.source_rows
        top_n = min(REFERENCE_TOP, n)
        raw = [
            Insight(
                id=f"{view.id}-i0",
                dataset_id=view.dataset_id,
                view_id=view.id,
                fact_type="reference",
                fact=f"{view.title}: {n} {plural(n, ('строка', 'строки', 'строк'))}; "
                + "; ".join(view.notes[1:]),
                headline=f"{view.title}: в таблице топ-{top_n} из {n} позиций",
                strength=0.45,
                evidence=list(view.evidence),
                numbers=[str(top_n), str(n)],
            )
        ]
    else:
        raw = []
    return [guard_insight(ins, view=view) for ins in raw]


def missing_insights(dataset_id: str, prof: TableProfile, ref: str) -> list[Insight]:
    """Факт о пропусках: в выводах честно говорим, чего нет в данных."""
    gaps = [c for c in prof.columns if c.missing and c.missing_ratio >= 0.01]
    if not gaps:
        return []
    worst = max(gaps, key=lambda c: c.missing_ratio)
    pct = fmt_number(worst.missing_ratio * 100, decimals=0)
    missing_labels: list[str] = []
    if prof.time_column and prof.time_column != worst.name:
        tcol = prof.column(prof.time_column)
        vals = worst.numbers if worst.is_metric else worst.labels
        for idx, val in enumerate(vals):
            if val is None and idx < len(tcol.periods) and tcol.periods[idx] is not None:
                lbl = tcol.periods[idx].label
                if lbl not in missing_labels:
                    missing_labels.append(lbl)
    detail = f" (нет данных за {', '.join(missing_labels[:4])})" if missing_labels else ""
    return [
        Insight(
            id=f"{dataset_id}-missing",
            dataset_id=dataset_id,
            fact_type="missing",
            fact=f"Пропуски в «{worst.label}»: нет значений в {pct}% строк{detail}",
            headline=f"Пропуски: «{worst.label}» не заполнено в {pct}% строк",
            strength=0.2,
            evidence=[ref],
            numbers=[pct],
        )
    ]


_IGNORE_CATS = {"остальные", "прочие", "прочее", "итого", "всего", "other", "total"}


def cross_table_insights(
    items: list[tuple[str, str, list[DataView]]],
) -> list[Insight]:
    """Межтабличный факт: максимум одна связка на пакет данных и только
    при явной общей оси (общие периоды >= 4 или общие категории >= 3).

    ``items`` — список кортежей ``(dataset_id, dataset_title, views)``."""
    if len(items) < 2:
        return []
    candidates: list[tuple[float, Insight]] = []

    for idx_a in range(len(items)):
        ds_a_id, ds_a_title, views_a = items[idx_a]
        for idx_b in range(idx_a + 1, len(items)):
            ds_b_id, ds_b_title, views_b = items[idx_b]

            # 1. Явная общая временная ось (SERIES)
            series_a = next(
                (
                    v
                    for v in views_a
                    if v.shape == SERIES and v.category_kind == "time" and v.metric
                ),
                None,
            )
            series_b = next(
                (
                    v
                    for v in views_b
                    if v.shape == SERIES and v.category_kind == "time" and v.metric
                ),
                None,
            )
            if series_a is not None and series_b is not None:
                map_a = {
                    cat: val
                    for cat, val in zip(series_a.categories, series_a.metric.values, strict=False)
                    if val is not None
                }
                map_b = {
                    cat: val
                    for cat, val in zip(series_b.categories, series_b.metric.values, strict=False)
                    if val is not None
                }
                common_t = [c for c in series_a.categories if c in map_a and c in map_b]
                if len(common_t) >= 4:
                    xs = [map_a[c] for c in common_t]
                    ys = [map_b[c] for c in common_t]
                    r = pearson(xs, ys)
                    if r is not None and abs(r) >= 0.6:
                        rtxt = fmt_number(r, decimals=2)
                        na = _metric_title(series_a.metric.name)
                        nb = _metric_title(series_b.metric.name)
                        na_mid = na if na.isupper() else na.casefold()
                        nb_mid = nb if nb.isupper() else nb.casefold()
                        direction = "синхронно" if r > 0 else "в противофазе"
                        n_c = len(common_t)
                        p_word = _plural(n_c, ("период", "периода", "периодов"))
                        ins = Insight(
                            id=f"{ds_a_id}-{ds_b_id}-cross",
                            dataset_id=ds_a_id,
                            view_id=series_a.id,
                            fact_type="cross_table",
                            fact=(
                                f"Межтабличная связь «{na}» и «{nb}»: "
                                f"{na_mid} и {nb_mid} меняются {direction} "
                                f"(r = {rtxt} по {n_c} общим периодам)"
                            ),
                            headline=(
                                f"{na} и {nb_mid} движутся {direction}: "
                                f"r = {rtxt} за {n_c} {p_word}"
                            ),
                            strength=round(min(0.92, abs(r)), 3),
                            evidence=list(series_a.evidence[:1]) + list(series_b.evidence[:1]),
                            numbers=[rtxt, str(n_c)],
                        )
                        candidates.append((abs(r), ins))
                        continue

            # 2. Явная общая категориальная ось (CATEGORICAL)
            cat_a = next(
                (
                    v
                    for v in views_a
                    if v.shape == CATEGORICAL and not v.derived and not v.bridge and v.metric
                ),
                None,
            )
            cat_b = next(
                (
                    v
                    for v in views_b
                    if v.shape == CATEGORICAL and not v.derived and not v.bridge and v.metric
                ),
                None,
            )
            if cat_a is not None and cat_b is not None:
                map_a = {
                    c.strip().casefold(): (c.strip(), val)
                    for c, val in zip(cat_a.categories, cat_a.metric.values, strict=False)
                    if val is not None and c.strip().casefold() not in _IGNORE_CATS
                }
                map_b = {
                    c.strip().casefold(): (c.strip(), val)
                    for c, val in zip(cat_b.categories, cat_b.metric.values, strict=False)
                    if val is not None and c.strip().casefold() not in _IGNORE_CATS
                }
                common_k = [k for k in map_a if k in map_b]
                min_cats = max(1, min(len(map_a), len(map_b)))
                if len(common_k) >= 3 and len(common_k) >= 0.5 * min_cats:
                    tot_a = sum(v for _, v in map_a.values())
                    tot_b = sum(v for _, v in map_b.values())
                    top_a_k = max(map_a, key=lambda k: map_a[k][1])
                    top_b_k = max(map_b, key=lambda k: map_b[k][1])
                    if top_a_k == top_b_k and tot_a > 0 and tot_b > 0:
                        lbl = map_a[top_a_k][0]
                        sh_a = fmt_number(map_a[top_a_k][1] / tot_a * 100, decimals=0)
                        sh_b = fmt_number(map_b[top_b_k][1] / tot_b * 100, decimals=0)
                        n_c = len(common_k)
                        score = (map_a[top_a_k][1] / tot_a + map_b[top_b_k][1] / tot_b) / 2.0
                        ins = Insight(
                            id=f"{ds_a_id}-{ds_b_id}-cross",
                            dataset_id=ds_a_id,
                            view_id=cat_a.id,
                            fact_type="cross_table",
                            fact=(
                                f"Межтабличное сравнение «{ds_a_title}» и «{ds_b_title}»: "
                                f"{lbl} лидирует в обоих срезах ({sh_a}% и {sh_b}% "
                                f"по {n_c} общим категориям)"
                            ),
                            headline=f"{lbl} лидирует в обоих срезах: {sh_a}% и {sh_b}%",
                            strength=round(min(0.88, max(0.6, score)), 3),
                            evidence=list(cat_a.evidence[:1]) + list(cat_b.evidence[:1]),
                            numbers=[sh_a, sh_b, str(n_c)],
                        )
                        candidates.append((score + 0.2, ins))

    if not candidates:
        return []
    candidates.sort(key=lambda pair: -pair[0])
    return [candidates[0][1]]


__all__ = ["cross_table_insights", "genitive", "missing_insights", "pearson", "view_insights"]
