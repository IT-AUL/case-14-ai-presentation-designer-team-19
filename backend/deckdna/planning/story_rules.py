"""Эталонная реализация правил Story rules (прототип для переноса в
backend/deckdna/planning/data_story.py и analysis/).

Модуль самодостаточен (только stdlib) — каждый алгоритм из отчёта
проверен тестами test_story_rules.py. Он НЕ подключён к бэкенду: перенос —
отдельная задача (см. раздел «План внедрения» отчёта).

Разделы:
  A. Русская морфология-минимум: форма числительного, род, глагол.
  B. Формулы инсайтов: Δ, CAGR, тренд (OLS/R²), доли, Pareto, HHI, вклад.
  C. Action Title: сборка по шаблону + детерминированный валидатор.
  D. so_what: действие по типу факта.
  E. Выбор колонок Compact Table и расчёт геометрии (≥1 800 000 EMU, ≥11pt).
  F. Выбор архетипа слайда.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict as _ConfigDict
from pydantic import Field as _Field

# ──────────────────────────── A. морфология-минимум ────────────────────────────


def plural(n: int, forms: tuple[str, str, str]) -> str:
    n10, n100 = abs(n) % 10, abs(n) % 100
    if n10 == 1 and n100 != 11:
        return forms[0]
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return forms[1]
    return forms[2]


def fmt_num(value: float, decimals: int | None = None) -> str:
    """«1 234,5», «12», «0,35»; минус — настоящий «−» (U+2212)."""
    if decimals is None:
        a = abs(value)
        decimals = 0 if a >= 100 or float(value).is_integer() else 1 if a >= 10 else 2
    text = f"{abs(value):,.{decimals}f}".replace(",", "\u00a0").replace(".", ",")
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    neg = value < 0 and any(ch in "123456789" for ch in text)
    return ("\u2212" if neg else "") + text


def fmt_signed_pct(value: float, decimals: int = 0) -> str:
    """«+5%» / «−79%»: знак всегда один (в бэкенде был «+-79%»)."""
    text = fmt_num(value, decimals)
    return f"{text}%" if text.startswith("\u2212") else f"+{text}%"


def times_phrase(ratio: float) -> str:
    """«в 2,1 раза», «в 2 раза», «в 5 раз», «в 12 раз»: целое ≥5 — «раз»,
    дробное — всегда «раза» (в 1,5 / 2,1 / 12,4 раза)."""
    if abs(ratio - round(ratio)) < 0.05:
        n = int(round(ratio))
        return f"в {n} {plural(n, ('раз', 'раза', 'раз'))}"
    return f"в {fmt_num(ratio, 1)} раза"


_ADJ_END = ("ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие")
_F_SOFT = {"прибыль", "сеть", "цепь", "тень"}


def head_gender(metric: str) -> str:
    """Род/число по главному существительному метки: m / f / n / p.
    «Средний чек» → m, «Новые клиенты» → p, «Число записей» → n,
    «Выручка B2B» → f, латиница (NPS, ROI) → m."""
    words = [w for w in re.findall(r"[A-Za-zА-Яа-яЁё]+", metric)]
    head = next(
        (w for w in words if not w.casefold().endswith(_ADJ_END)), words[0] if words else ""
    )
    low = head.casefold()
    if not re.search(r"[а-яё]", low):
        return "m"
    if low in _F_SOFT or low.endswith("ость"):
        return "f"
    last = low[-1]
    if last in "ая":
        return "f"
    if last in "ое":
        return "n"
    if last in "ыи":
        return "p"
    return "m"


_VERBS = {
    "grow": {"m": "вырос", "f": "выросла", "n": "выросло", "p": "выросли"},
    "fall": {"m": "снизился", "f": "снизилась", "n": "снизилось", "p": "снизились"},
    "flat": {"m": "остался", "f": "осталась", "n": "осталось", "p": "остались"},
    "peak": {"m": "достиг", "f": "достигла", "n": "достигло", "p": "достигли"},
}


def verb(kind: str, metric: str) -> str:
    return _VERBS[kind][head_gender(metric)]


# ──────────────────────────── B. формулы инсайтов ──────────────────────────────


@dataclass(frozen=True)
class Delta:
    a: float
    b: float
    abs: float
    pct: float | None  # None, если база ≤ 0 (процент не определён)
    ratio: float | None
    kind: str  # growth | decline | flat | undefined


FLAT_PCT = 5.0  # |Δ%| < 5 — «без заметного тренда» (как в бэкенде)


def delta(a: float, b: float) -> Delta:
    """Δ абсолютная, %, разы. База ≤ 0 → процент не определён (нельзя
    «рост на 300%» от отрицательной или нулевой базы)."""
    if a is None or b is None:
        raise ValueError("a и b обязательны")
    if a <= 0:
        return Delta(a, b, b - a, None, None, "undefined")
    ratio = b / a
    pct = (ratio - 1) * 100
    kind = "flat" if abs(pct) < FLAT_PCT else "growth" if pct > 0 else "decline"
    return Delta(a, b, b - a, pct, ratio, kind)


def cagr(a: float, b: float, periods: int) -> float | None:
    """Среднегодовой/среднепериодный темп: (b/a)^(1/(n−1)) − 1. Только
    при a,b>0 и ≥4 точках: по двум точкам CAGR — это та же дельта."""
    if periods < 4 or a <= 0 or b <= 0:
        return None
    return ((b / a) ** (1 / (periods - 1)) - 1) * 100


def ols(values: list[float]) -> tuple[float, float]:
    """(наклон на период, R²) линейной регрессии по индексу периода."""
    n = len(values)
    xs = range(n)
    mx, my = (n - 1) / 2, sum(values) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, values, strict=True))
    syy = sum((y - my) ** 2 for y in values)
    if sxx == 0 or syy == 0:
        return 0.0, 0.0
    slope = sxy / sxx
    return slope, (sxy * sxy) / (sxx * syy)


def trend_character(values: list[float]) -> str:
    """steady (R²≥0,6), choppy (R²<0,6) — чтобы не писать «стабильный
    рост» по двум точкам; < 5 точек → 'unknown'."""
    clean = [v for v in values if v is not None]
    if len(clean) < 5:
        return "unknown"
    return "steady" if ols(clean)[1] >= 0.6 else "choppy"


@dataclass
class ShareStats:
    leader: str
    leader_share: float  # %
    runner_up: str | None
    gap_pp: float | None  # п.п. между 1 и 2
    lead_ratio: float | None  # во сколько раз 1-й больше 2-го
    laggard: str
    laggard_share: float
    top3_share: float
    hhi: float  # 0..1
    effective_n: float
    pareto_k: int  # сколько позиций набирают ≥80%
    pareto_n: int
    pareto_items_pct: float
    pareto_holds: bool  # k/n ≤ 30% и n ≥ 5
    cum: list[float] = field(default_factory=list)


def share_stats(pairs: list[tuple[str, float]]) -> ShareStats | None:
    """Лидер/аутсайдер/концентрация. Отрицательные значения исключают
    расчёт долей (сумма не имеет смысла) → None."""
    pairs = [(c, v) for c, v in pairs if v is not None]
    if not pairs or any(v < 0 for _, v in pairs):
        return None
    total = sum(v for _, v in pairs)
    if total <= 0:
        return None
    pairs.sort(key=lambda kv: -kv[1])
    shares = [v / total for _, v in pairs]
    cum, acc = [], 0.0
    for s in shares:
        acc += s
        cum.append(acc)
    k = next(i + 1 for i, c in enumerate(cum) if c >= 0.8 - 1e-9)
    n = len(pairs)
    hhi = sum(s * s for s in shares)
    second = pairs[1] if n > 1 else None
    return ShareStats(
        leader=pairs[0][0],
        leader_share=shares[0] * 100,
        runner_up=second[0] if second else None,
        gap_pp=(shares[0] - shares[1]) * 100 if second else None,
        lead_ratio=pairs[0][1] / second[1] if second and second[1] > 0 else None,
        laggard=pairs[-1][0],
        laggard_share=shares[-1] * 100,
        top3_share=sum(shares[:3]) * 100,
        hhi=hhi,
        effective_n=1 / hhi,
        pareto_k=k,
        pareto_n=n,
        pareto_items_pct=k / n * 100,
        pareto_holds=n >= 5 and k / n <= 0.3,
        cum=[c * 100 for c in cum],
    )


def contributions(
    before: dict[str, float], after: dict[str, float]
) -> list[tuple[str, float, float]]:
    """Вклад в изменение итога: [(категория, Δ, доля от ΣΔ в %)], по |Δ|.
    Если ΣΔ≈0 доли не определены (вернуть долю 0)."""
    keys = set(before) | set(after)
    deltas = {k: after.get(k, 0.0) - before.get(k, 0.0) for k in keys}
    total = sum(deltas.values())
    out = [(k, d, (d / total * 100 if abs(total) > 1e-12 else 0.0)) for k, d in deltas.items()]
    return sorted(out, key=lambda t: -abs(t[1]))


def is_real_time_series(
    *, n_rows: int, n_buckets: int, time_regular: bool, has_entity_key: bool
) -> bool:
    """Охрана от ложной динамики: колонка дат — атрибут сущности («Дата
    найма», «Дата договора»), а не ось времени метрики. Ряд настоящий, если
    (а) даты образуют регулярный шаг, или (б) на период приходится в среднем
    ≥ 3 наблюдений (транзакции), или (в) нет ключа сущности (колонки с
    уникальным значением на строку) и точек ≥ 5."""
    if n_buckets < 3:
        return False
    if time_regular:
        return True
    if n_rows / max(n_buckets, 1) >= 3:
        return True
    return not has_entity_key and n_buckets >= 5


# ──────────────────────────── C. Action Title ─────────────────────────────────

TITLE_MIN, TITLE_MAX = 25, 95  # символов: ≤ 2 строки на 28–32pt в 16:9
LEAD_MAX = 140

_BANNED_EXACT = re.compile(
    r"^\s*(чистый шаблон|шаблон|справочная таблица|справочник|таблица|данные|данные по .*|"
    r"данные из файла.*|слайд\s*\d*|без названия|untitled|title|sheet\s*\d*|лист\s*\d*|"
    r"ключевые показатели|ключевые цифры|основные показатели|динамика(\s+\S+)?|структура(\s+\S+)?|"
    r"сравнение(\s+\S+)?|обзор|результаты|итоги|анализ(\s+\S+)?)\s*[.:!]?\s*$",
    re.I,
)
_BANNED_DUMP = re.compile(
    r"(справочная таблица|reference table|\bпозици[яий]\b.*:|:\s*\w+(,\s*\w+){3,}|"
    r"\d+\s+(строк|строки|строка|колон\w+|позици\w+)\s*[.:]?\s*$)",
    re.I,
)
_FILE_LIKE = re.compile(
    r"^[\w\-]+\.(csv|xlsx?|json|txt|md|pptx?)$|^[A-Za-z]+\d+$|^ds-[0-9a-f]+", re.I
)
_LABEL_COLON = re.compile(r"^[^:—–]{1,40}:\s+\S")
# Корни глаголов/глагольных связок, допустимых как «вывод»; поиск по основе,
# чтобы не тянуть морфологию. Список — расширяемый конфиг, не код-инвариант.
_VERB_STEMS = (
    "вырос|выросл|рос[л]|снизил|сократил|упал|упала|упали|вырастет|превыш|опережа|отстаёт|отстает|"
    "формир|даёт|дает|дают|обеспечива|составля|концентрир|приход|принос|сниж|ускорил|замедлил|"
    "достиг|остал|удвоил|утроил|потерял|привлёк|привлек|зависит|связан|превысил|"
    "требует|нужн|стоит|позволя|сэконом|окупа|раскрыва|показыва|подтвержда|дали|даст|"
    "ведёт|ведет|держит|растёт|растет|падает|увелич|уменьш|растут|падают"
)
_VERB_RE = re.compile(rf"\b(?:{_VERB_STEMS})\w*", re.I)
_NUM_RE = re.compile(r"(?<!\w)[−-]?\d[\d\u00a0\s]*(?:[.,]\d+)?")


def numbers_in(text: str) -> set[str]:
    """Нормализованные числа текста: '1 234,5' → '1234.5', '−79' → '-79'."""
    out = set()
    for m in _NUM_RE.finditer(text.replace("\u2212", "-")):
        raw = re.sub(r"[\s\u00a0]", "", m.group(0)).replace(",", ".")
        raw = raw.lstrip("+")
        try:
            v = float(raw)
        except ValueError:
            continue
        out.add(str(int(v)) if v.is_integer() else str(v))
    return out


@dataclass
class TitleVerdict:
    ok: bool
    reasons: list[str]
    score: int  # 0..5


def validate_action_title(
    title: str, allowed_numbers: set[str] | None = None, *, data_slide: bool = True
) -> TitleVerdict:
    """Детерминированный гейт заголовка. Отклоняет заглушки/темы/дампы;
    для слайда данных требует число из фактов и глагол/тире-вывод."""
    reasons: list[str] = []
    t = (title or "").strip()
    if _BANNED_EXACT.match(t):
        reasons.append("заглушка/тема вместо вывода")
    if _BANNED_DUMP.search(t):
        reasons.append("технический дамп (справочник/счётчик строк/список колонок)")
    if _FILE_LIKE.match(t):
        reasons.append("имя файла/листа вместо заголовка")
    if not TITLE_MIN <= len(t) <= TITLE_MAX:
        reasons.append(f"длина {len(t)} вне {TITLE_MIN}–{TITLE_MAX}")
    if t.endswith(":"):
        reasons.append("заголовок оборван на двоеточии")
    if _LABEL_COLON.match(t) and not _VERB_RE.search(t.split(":", 1)[0]):
        reasons.append("ярлык с двоеточием («Метрика: …») вместо предложения-вывода")
    has_verb = bool(_VERB_RE.search(t)) or bool(re.search(r"\s[—–]\s", t))
    nums = numbers_in(t)
    if not has_verb:
        reasons.append("нет глагола/вывода")
    if data_slide and not nums:
        reasons.append("нет числа")
    if allowed_numbers is not None and nums - allowed_numbers:
        reasons.append("числа не из фактов: " + ", ".join(sorted(nums - allowed_numbers)))
    score = sum(
        [
            not _BANNED_EXACT.match(t) and not _BANNED_DUMP.search(t),
            TITLE_MIN <= len(t) <= TITLE_MAX,
            has_verb,
            bool(nums) or not data_slide,
            not (_LABEL_COLON.match(t) and not _VERB_RE.search(t.split(":", 1)[0])),
        ]
    )
    return TitleVerdict(not reasons, reasons, score)


_PERIOD_FORMS = {
    "month": ("месяц", "месяца", "месяцев"),
    "quarter": ("квартал", "квартала", "кварталов"),
    "week": ("неделя", "недели", "недель"),
    "year": ("год", "года", "лет"),
}


def period_phrase(n: int, grain: str) -> str:
    return f"{n} {plural(n, _PERIOD_FORMS[grain])}"


def title_trend(
    metric: str, d: Delta, n_periods: int, grain: str, driver: str | None = None
) -> str | None:
    """Action Title для динамики: [Метрика] [глагол] [Δ] за [период] (— [драйвер])."""
    if d.kind == "undefined" or d.pct is None or d.ratio is None:
        return None
    span = f"за {period_phrase(n_periods, grain)}"
    if d.kind == "flat":
        head = f"{metric} {verb('flat', metric)} на уровне {fmt_num(d.b)} {span}"
    elif d.kind == "growth":
        how = times_phrase(d.ratio) if d.ratio >= 1.5 else f"на {fmt_num(d.pct, 0)}%"
        head = f"{metric} {verb('grow', metric)} {how} {span}"
    else:
        how = times_phrase(1 / d.ratio) if d.ratio <= 0.5 else f"на {fmt_num(-d.pct, 0)}%"
        head = f"{metric} {verb('fall', metric)} {how} {span}"
    if driver:
        head += f" — главный вклад: {driver}"
    return head


def leader_is_meaningful(st: ShareStats) -> bool:
    """Заголовок про лидера допустим, только если он реально выделяется:
    доля ≥ 25% и ≥ 1,5× от «справедливой» 1/n. Иначе — «поровну», заголовка
    про лидера нет (в бэкенде «Поставщик 1: 9% записей» из 11 поровну)."""
    return st.leader_share >= 25 and st.leader_share >= 1.5 * 100 / st.pareto_n


def _plural_label(label: str) -> bool:
    low = label.strip().casefold()
    return bool(low) and low[-1] in "ыи" and not low.endswith(("ий", "ый"))


def title_share(
    dimension: str,
    leader: str,
    share_pct: float,
    metric_phrase: str,
    pareto: ShareStats | None = None,
    *,
    percent_given: bool = False,
) -> str:
    """Action Title для структуры. Подлежащее — «{Измерение} «{лидер}»»:
    кавычки снимают склонение значений («Регион «Тверь» формирует…»), а
    настоящее время не зависит от рода. Если измерение во мн. числе или
    пусто — тире-конструкция."""
    if pareto and pareto.pareto_holds:
        return (
            f"{pareto.pareto_k} из {pareto.pareto_n} позиций "
            f"дают {fmt_num(pareto.cum[pareto.pareto_k - 1], 0)}% {metric_phrase}"
        )
    n = fmt_num(share_pct, 0)
    subject = (
        f"{dimension.strip()} «{leader}»"
        if dimension.strip() and not _plural_label(dimension)
        else None
    )
    if percent_given:
        return (
            f"{subject} занимает {n}% — крупнейшая доля"
            if subject
            else f"«{leader}» — крупнейшая доля: {n}%"
        )
    return (
        f"{subject} формирует {n}% {metric_phrase}"
        if subject
        else f"«{leader}» — {n}% {metric_phrase}"
    )


def title_value_split(column: str, value: str, k: int, n: int) -> str:
    """Заголовок справочника без метрик — из распределения категории:
    «У 2 из 3 позиций статус — «идёт»». Подлежащее не склоняем: значение в
    кавычках, существительное-сущность не нужно («позиций» нейтрально)."""
    p_label = plural(n, ("позиции", "позиций", "позиций"))
    return f"У {k} из {n} {p_label} {column.strip().casefold()} — «{value}»"


def title_gap(leader: str, runner_up: str, ratio: float) -> str:
    return f"{leader} опережает «{runner_up}» {times_phrase(ratio)}"


# ──────────────────────────── D. so_what ──────────────────────────────────────


def so_what(
    fact_type: str,
    *,
    subject: str = "",
    driver: str | None = None,
    driver_pct: float | None = None,
    share: float | None = None,
    laggard: str | None = None,
    laggard_share: float | None = None,
    top_k: int | None = None,
    top_share: float | None = None,
    tail_n: int | None = None,
    pct: float | None = None,
    r: float | None = None,
) -> str | None:
    """«Что делать дальше»: императив + названный объект из данных +
    число из факта. Нет нужного слота — None (а не общая фраза): слайд без
    so_what честнее слайда с отпиской. Все числа — только из аргументов."""
    s = f"«{subject}»" if subject else "показателя"
    if fact_type == "growth" and driver and driver_pct is not None:
        return f"Усилить «{driver}»: на него приходится {fmt_num(driver_pct, 0)}% прироста"
    if fact_type == "growth" and pct is not None:
        return (
            f"Закрепить рост {s}: заложить +{fmt_num(pct, 0)}% как базу плана и найти его источник"
        )
    if fact_type == "decline" and driver and driver_pct is not None:
        return f"Остановить потери в «{driver}»: это {fmt_num(abs(driver_pct), 0)}% снижения"
    if fact_type == "decline" and pct is not None:
        return f"Разобрать причины падения {s} на {fmt_num(abs(pct), 0)}% до следующего периода"
    if fact_type == "concentration" and top_k and top_share is not None and tail_n:
        return (
            f"Защитить топ-{top_k} ({fmt_num(top_share, 0)}% результата) "
            f"и решить, что делать с остальными {tail_n}"
        )
    if fact_type == "leader" and driver and share is not None:
        if share >= 50:
            return f"Снизить зависимость от «{driver}» — на него {fmt_num(share, 0)}% результата"
        return f"Опереться на «{driver}» ({fmt_num(share, 0)}%) и подтянуть отстающих"
    if fact_type == "laggard" and laggard and laggard_share is not None:
        pct_str = fmt_num(laggard_share, 0)
        return f"Решить по «{laggard}» ({pct_str}%): развивать или выводить из фокуса"
    if fact_type == "flat" and pct is not None:
        return f"Искать новый драйвер для {s}: за период изменение лишь {fmt_signed_pct(pct, 1)}"
    if fact_type == "correlation" and r is not None:
        return f"Проверить причинность связи (r = {fmt_num(r, 2)}) пилотом до пересмотра бюджета"
    return None


# ──────────────────────────── E. Compact Table ────────────────────────────────

MIN_COL_EMU = 1_800_000  # ≈ 4,6 см
MAX_TABLE_COLS = 4
MIN_TABLE_FONT_PT = 11.0
MAX_TABLE_ROWS = 7  # вместе со строкой «Прочие»; как density.max_table_rows
EMU_PER_PT = 12_700
_CELL_INSET_LR = 91_440
_AVG_CYR_EM = 0.62  # калибровка по audit.basic._text_width_emu: 0,61–0,67 em на знак
_LONGEST_WORD_LIMIT = 18  # знаков в слове без разрыва при 11pt и 1,8 млн EMU

# порядок = бизнес-приоритет главной метрики (раньше — важнее)
_METRIC_PRIORITY = (
    r"выручк|revenue|sales|продаж|оборот",
    r"прибыл|profit|маржа|ebitda",
    r"сумм|amount|доход|расход|затрат|cost|стоимост|цена|price|бюджет",
    r"план|факт|оклад|объ[её]м",
)
_METRIC_NAME = re.compile("|".join(_METRIC_PRIORITY), re.I)


def metric_priority_bonus(name: str) -> float:
    for i, pat in enumerate(_METRIC_PRIORITY):
        if re.search(pat, name, re.I):
            return 0.3 - 0.05 * i
    return 0.0


_STATUS_NAME = re.compile(
    r"(статус|этап|стадия|риск|приоритет|status|stage|priority|risk|состояние)", re.I
)
_ENTITY_NAME = re.compile(
    r"(^|\s)(имя|фио|название|наименование|человек|сотрудник|клиент|продукт|товар|проект|регион|"
    r"гипотез\w*|направлен\w*|name|title|product|client|region|project)($|\s)",
    re.I,
)
_ACTION_NAME = re.compile(r"(след\w+ шаг|действие|рекомендац|вывод|next|action|решение)", re.I)
_BALLAST_NAME = re.compile(
    r"(комментар|примечан|описание|заметк|note|comment|remark|id$|^id\b|uuid|^№$)", re.I
)


@dataclass
class Col:
    """Минимальное описание колонки для отбора (из ColumnProfile)."""

    name: str
    kind: str  # money | number | percent | category | text | id | period
    cardinality: int
    missing_ratio: float
    avg_len: float = 0.0  # для текстовых
    cv: float = 0.0  # коэф. вариации для метрик
    unit: str | None = None


@dataclass
class ColumnPlan:
    role: str  # entity | metric | delta | status | context
    name: str
    derived: bool = False
    score: float = 0.0


def _is_metric(c: Col) -> bool:
    return c.kind in ("money", "number", "percent")


def score_column(c: Col, n_rows: int) -> tuple[str, float] | None:
    """(роль, балл) или None, если колонка — балласт (в таблицу не идёт)."""
    if c.missing_ratio > 0.5 or c.cardinality <= 1 and n_rows > 1:
        return None
    if _BALLAST_NAME.search(c.name.strip()) and not _ENTITY_NAME.search(c.name):
        return None
    if c.kind == "text" and c.avg_len > 40 and not _ACTION_NAME.search(c.name):
        return None
    if _is_metric(c):
        base = {"money": 1.0, "number": 0.8, "percent": 0.7}[c.kind]
        base = base * (0.7 + 0.3 * min(1.0, c.cv / 0.5))
        return "metric", base + metric_priority_bonus(c.name)
    if (
        c.kind in ("category", "text", "id")
        and 2 <= c.cardinality <= 6
        and _STATUS_NAME.search(c.name)
    ):
        return "status", 0.95
    if c.kind in ("category", "text") and _ACTION_NAME.search(c.name) and c.avg_len <= 60:
        return "context", 0.7
    if c.kind == "category" and 2 <= c.cardinality <= 8:
        return "context", 0.5
    if c.kind == "text" and _ACTION_NAME.search(c.name):
        return "context", 0.6
    if c.kind == "period":
        return "context", 0.35
    if c.kind == "text" and c.avg_len <= 40:
        return "context", 0.3
    return None


def pick_entity(cols: list[Col], n_rows: int) -> Col | None:
    """Колонка-сущность: подпись строки. Приоритет: имя-подсказка, затем
    самая «уникальная» из текстовых/категориальных (≥ 80% строк), короткая."""
    cand = [
        c
        for c in cols
        if c.kind in ("text", "category", "id")
        and c.avg_len <= 40
        and c.cardinality >= max(2, 0.8 * n_rows)
        and c.missing_ratio <= 0.2
    ]
    if not cand:
        cand = [c for c in cols if c.kind in ("category", "text") and c.cardinality >= 2]
    if not cand:
        cand = [c for c in cols if c.kind == "period"]  # подпись строки — период
    if not cand:
        return None
    return max(
        cand,
        key=lambda c: (
            bool(_ENTITY_NAME.search(c.name)),
            c.kind != "id",
            c.cardinality / max(n_rows, 1),
        ),
    )


def max_columns(host_width_emu: int, cap: int = MAX_TABLE_COLS) -> int:
    """Сколько колонок помещается при минимальной ширине 1 800 000 EMU."""
    return max(0, min(cap, host_width_emu // MIN_COL_EMU))


def select_table_columns(
    cols: list[Col],
    n_rows: int,
    host_width_emu: int,
    *,
    delta_pair: tuple[str, str] | None = None,
) -> tuple[list[ColumnPlan], list[str]]:
    """Отбор ≤4 колонок: Сущность + Главная метрика + Динамика/Δ + Статус.

    delta_pair — имена (до, после) двух метрик с одной единицей, из которых
    собирается производная колонка «Δ, %» (не показывается, если база ≤ 0).
    Возвращает (план колонок в порядке показа, имена скрытых колонок).
    Пустой план = «таблица не годится» → использовать другой архетип."""
    limit = max_columns(host_width_emu)
    if limit < 2:
        return [], [c.name for c in cols]
    entity = pick_entity(cols, n_rows)
    scored: dict[str, tuple[str, float]] = {}
    for c in cols:
        if entity is not None and c.name == entity.name:
            continue
        s = score_column(c, n_rows)
        if s:
            scored[c.name] = s
    plan: list[ColumnPlan] = []
    if entity is not None:
        plan.append(ColumnPlan("entity", entity.name, score=1.0))

    def best(role: str, exclude: set[str]) -> str | None:
        pool = [(n, s) for n, (r, s) in scored.items() if r == role and n not in exclude]
        return max(pool, key=lambda t: t[1])[0] if pool else None

    used = {p.name for p in plan}
    metric = best("metric", used)
    if metric:
        plan.append(ColumnPlan("metric", metric, score=scored[metric][1]))
        used.add(metric)
    if delta_pair and all(p in scored or p == metric for p in delta_pair) and len(plan) < limit:
        plan.append(
            ColumnPlan("delta", f"Δ {delta_pair[1]} к {delta_pair[0]}, %", derived=True, score=0.9)
        )
        used |= set(delta_pair)
    status = best("status", used)
    if status and len(plan) < limit:
        plan.append(ColumnPlan("status", status, score=scored[status][1]))
        used.add(status)
    while len(plan) < limit:
        nxt = None
        pool = sorted(
            ((n, s) for n, (r, s) in scored.items() if n not in used), key=lambda t: -t[1]
        )
        if pool:
            nxt = pool[0][0]
        if nxt is None:
            break
        role = scored[nxt][0]
        if role == "status" and any(p.role == "status" for p in plan):
            role = "context"  # вторая «статусная» колонка — просто контекст
        plan.append(ColumnPlan(role, nxt, score=scored[nxt][1]))
        used.add(nxt)
    order = {"entity": 0, "metric": 1, "delta": 2, "status": 3, "context": 4}
    plan.sort(key=lambda p: order[p.role])
    shown = {p.name for p in plan} | {x for p in plan if p.derived for x in (delta_pair or ())}
    hidden = [c.name for c in cols if c.name not in shown]
    return plan, hidden


def table_footnote(shown: int, total: int, rows_shown: int, rows_total: int) -> str:
    cols = f"{shown} из {total} {plural(total, ('колонки', 'колонок', 'колонок'))}"
    base = f"Показаны {cols}"
    if rows_total > rows_shown:
        base += f", {rows_shown} из {rows_total} {plural(rows_total, ('строки', 'строк', 'строк'))}"
    return base + ". Полная таблица — в приложении и HTML-экспорте"


def top_rows_with_other(
    rows: list[list], metric_idx: int, limit: int = MAX_TABLE_ROWS
) -> list[list]:
    """Топ-(limit−1) по метрике + строка «Прочие (n)» с суммой метрики."""
    if len(rows) <= limit:
        return rows
    ranked = sorted(
        rows, key=lambda r: -(r[metric_idx] if isinstance(r[metric_idx], (int, float)) else 0)
    )
    head, tail = ranked[: limit - 1], ranked[limit - 1 :]
    other = [""] * len(rows[0])
    other[0] = f"Прочие ({len(tail)})"
    other[metric_idx] = sum(r[metric_idx] for r in tail if isinstance(r[metric_idx], (int, float)))
    return [*head, other]


def text_width_emu(text: str, size_pt: float) -> float:
    return len(text) * _AVG_CYR_EM * size_pt * EMU_PER_PT


def cell_breaks_word(text: str, col_w_emu: int, size_pt: float = MIN_TABLE_FONT_PT) -> bool:
    """True, если самое длинное слово не влезает в ширину ячейки без
    разрыва (источник «Че-ло-век»)."""
    usable = col_w_emu - 2 * _CELL_INSET_LR
    return any(text_width_emu(w, size_pt) > usable for w in text.split())


def fit_table(cells: list[list[str]], host_w_emu: int, host_h_emu: int, n_cols: int) -> dict:
    """Геометрия: ширина колонок ≥1,8 млн, кегль ≥11pt, строки ≤7.
    Возвращает {'col_w', 'font_pt', 'rows_fit', 'ok', 'problems'}."""
    problems: list[str] = []
    col_w = host_w_emu // max(n_cols, 1)
    if col_w < MIN_COL_EMU:
        problems.append("колонка уже 1 800 000 EMU")
    for r in cells:
        for t in r:
            if cell_breaks_word(str(t), col_w):
                problems.append(f"слово рвётся: «{t[:20]}»")
                break
    size = MIN_TABLE_FONT_PT
    line_h = size * 1.2 * EMU_PER_PT
    # строка: максимум строк текста среди ячеек (жадный перенос по символам)
    usable_chars = max(1, int((col_w - 2 * _CELL_INSET_LR) / (_AVG_CYR_EM * size * EMU_PER_PT)))
    row_heights = []
    for r in cells:
        lines = max(math.ceil(len(str(t)) / usable_chars) or 1 for t in r)
        row_heights.append(2 * 45_720 + lines * line_h)
    total, rows_fit = 0.0, 0
    for h in row_heights:
        if total + h > host_h_emu:
            break
        total += h
        rows_fit += 1
    if rows_fit < len(cells):
        problems.append(f"в {host_h_emu} EMU помещается {rows_fit} из {len(cells)} строк при 11pt")
    return {
        "col_w": col_w,
        "font_pt": size,
        "rows_fit": rows_fit,
        "ok": not problems,
        "problems": problems,
    }


# ──────────────────────────── F. выбор архетипа ───────────────────────────────

ARCHETYPES = (
    "stat_callout",
    "card_grid",
    "comparison_split",
    "ibcs_chart",
    "compact_table",
    "process_flow",
)


def choose_archetype(
    *,
    shape: str,
    n_items: int,
    n_cols: int = 0,
    has_numeric: bool = False,
    has_pair: bool = False,
    sequential: bool = False,
    n_points: int = 0,
    host_width_emu: int = 10_500_000,
) -> str:
    """Детерминированный выбор одного из 6 архетипов (приоритет сверху вниз).

    shape: kpi | series | categorical | distribution | relationship | reference | text
    """
    if sequential and 3 <= n_items <= 4 and shape in ("text", "reference"):
        return "process_flow"
    if shape == "kpi" and n_items <= 2:
        return "stat_callout"
    if has_pair and n_items == 2:
        return "comparison_split"
    if shape in ("series", "categorical", "distribution", "relationship") and n_points >= 3:
        return "ibcs_chart"
    if shape == "kpi":
        return "card_grid"
    if shape in ("reference", "text"):
        if n_items == 3 and not has_numeric:
            return "card_grid"
        if 2 <= n_items <= 4 and n_cols > MAX_TABLE_COLS and not has_numeric:
            return "card_grid"
        if max_columns(host_width_emu) >= 3:
            return "compact_table"
        return "card_grid"
    return "stat_callout" if n_items == 1 else "card_grid"


# ─────────── G. Сторилайн SCQA, грамматика архетипов и разнообразие ────────────

SCQA_STAGES: tuple[str, ...] = (
    "situation",
    "complication",
    "answer",
    "evidence",
    "recommendation",
)

GRAMMAR_ARCHETYPES: tuple[str, ...] = (
    "kpi_cards",
    "big_number",
    "chart_takeaway",
    "comparison",
    "table_topn",
    "timeline",
    "process",
    "quote",
    "callout",
    "bullets",
)

VISUAL_ARCHETYPES: frozenset[str] = frozenset(
    {
        "kpi_cards",
        "big_number",
        "chart_takeaway",
        "comparison",
        "table_topn",
        "timeline",
        "process",
        "quote",
        "callout",
    }
)

_ARCHETYPE_TO_ROLE: dict[str, str] = {
    "kpi_cards": "kpi_card",
    "big_number": "big_number",
    "chart_takeaway": "takeaway",
    "comparison": "comparison_col",
    "table_topn": "table_note",
    "timeline": "timeline_step",
    "process": "step",
    "quote": "quote",
    "callout": "callout",
    "bullets": "bullet",
}

_ROLE_TO_ARCHETYPE: dict[str, str] = {v: k for k, v in _ARCHETYPE_TO_ROLE.items()}

_SCQA_SITUATION_RE = re.compile(
    r"(ситуаци|контекст|предпосылк|рынок|текущ\w*\s+состояни|обзор|введени|исходн\w*\s+данн|"
    r"background|context|overview|current\s+state)",
    re.I,
)
_SCQA_COMPLICATION_RE = re.compile(
    r"(проблем|осложнен|вызов|риск|барьер|узк\w*\s+мест|ограничен|сложност|дефицит|угроз|"
    r"потер|отставан|просадк|сбой|pain|problem|challenge|complication|bottleneck|risk)",
    re.I,
)
_SCQA_ANSWER_RE = re.compile(
    r"(ключев\w*\s+вывод|главн\w*\s+вывод|основн\w*\s+тезис|решени|ответ|подход|резюме|"
    r"итог\w*\s+вывод|key\s+finding|key\s+takeaway|solution|answer|executive\s+summary)",
    re.I,
)
_SCQA_RECOMMENDATION_RE = re.compile(
    r"(рекоменд|следующ\w*\s+шаг|план\s+действи|что\s+предлагаем|дорожн\w*\s+карт|внедрени|"
    r"призыв|целев\w*\s+шаг|next\s+step|action\s+plan|recommendation|roadmap|call\s+to\s+action)",
    re.I,
)

_COMPARISON_RE = re.compile(
    r"(сравнени|против|\bvs\b|до\s+и\s+после|было\s*[/—-]\s*стало|альтернатив|"
    r"преимуществ\w*\s+и\s+недостатк|вариант\s+[аa1]\b|колонка|comparison|versus)",
    re.I,
)
_TIMELINE_RE = re.compile(
    r"(дорожн\w*\s+карт|таймлайн|хронолог|истори|этап\w*\s+развития|\b20\d{2}\b|\bQ[1-4]\b|"
    r"квартал|год\w*|месяц|недел\w*\s+\d|фаза\s+\d|roadmap|timeline|milestone)",
    re.I,
)
_PROCESS_RE = re.compile(
    r"(процесс|шаг\s*\d|этап\s*\d|алгоритм|пайплайн|конвейер|поток|архитектур|механизм|"
    r"схема\s+работы|как\s+работает|workflow|pipeline|process|step\s+\d)",
    re.I,
)
_QUOTE_RE = re.compile(
    r"(цитат|отзыв|мнение|прямая\s+речь|клиент\s+о\s+нас|quote|testimonial|[«\"].{15,}[»\"])",
    re.I,
)
_CALLOUT_RE = re.compile(
    r"(важно|ключев\w*\s+тезис|главн\w*\s+мысль|фокус|вывод|инсайт|обратите\s+внимание|"
    r"callout|highlight|takeaway)",
    re.I,
)
_NUMERIC_TOKEN_RE = re.compile(
    r"(?:[+−-]?\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?\s*(?:%|п\.?\s*п\.?|₽|руб|млн|млрд|тыс|"
    r"x|х|раз\w*|ч|дн|мес|лет|год\w*|шт|ед|мс|сек|ГБ|ТБ|\$|€)?)",
    re.I,
)
_HAS_DIGIT_RE = re.compile(r"\d")


@dataclass(frozen=True)
class StorylineSettings:
    min_slides_for_agenda: int = 8
    closing_recommendation: bool = True
    max_bullets_per_slide: int = 5
    max_words_per_bullet: int = 12
    overflow_to_speaker_notes: bool = True
    no_adjacent_same_archetype: bool = True
    allow_adjacent_chart_if_different_type: bool = True
    max_bullets_archetype_share: float = 0.3334
    min_distinct_archetypes_for_8_plus: int = 4
    distinct_adjacent_donor_layouts: bool = True


@dataclass(frozen=True)
class VariantAxisSettings:
    density_level: str
    max_bullets_per_slide: int
    max_words_per_bullet: int
    visual_archetype_target_share: float
    max_bullets_archetype_share: float
    closing_bullets: int
    data_takeaway_bullets: int


def storyline_config(cfg: object | None = None) -> StorylineSettings:
    raw = getattr(cfg, "raw", None)
    if not isinstance(raw, dict):
        raw = cfg if isinstance(cfg, dict) else {}
    planning = raw.get("planning") if isinstance(raw, dict) else None
    story = (planning or {}).get("storyline") if isinstance(planning, dict) else None
    if not isinstance(story, dict):
        return StorylineSettings()
    density = story.get("density") if isinstance(story.get("density"), dict) else {}
    diversity = story.get("diversity") if isinstance(story.get("diversity"), dict) else {}
    return StorylineSettings(
        min_slides_for_agenda=int(story.get("min_slides_for_agenda", 8)),
        closing_recommendation=bool(story.get("closing_recommendation", True)),
        max_bullets_per_slide=int(density.get("max_bullets_per_slide", 5)),
        max_words_per_bullet=int(density.get("max_words_per_bullet", 12)),
        overflow_to_speaker_notes=bool(density.get("overflow_to_speaker_notes", True)),
        no_adjacent_same_archetype=bool(
            diversity.get(
                "forbid_adjacent_same_archetype",
                diversity.get("no_adjacent_same_archetype", True),
            )
        ),
        allow_adjacent_chart_if_different_type=bool(
            diversity.get(
                "allow_adjacent_chart_takeaway_if_different_type",
                diversity.get("allow_adjacent_chart_if_different_type", True),
            )
        ),
        max_bullets_archetype_share=float(
            diversity.get(
                "max_bullets_share",
                diversity.get("max_bullets_archetype_share", 0.3334),
            )
        ),
        min_distinct_archetypes_for_8_plus=int(
            diversity.get(
                "min_distinct_archetypes_for_long_deck",
                diversity.get("min_distinct_archetypes_for_8_plus", 4),
            )
        ),
        distinct_adjacent_donor_layouts=bool(
            diversity.get(
                "prefer_distinct_adjacent_donor_layouts",
                diversity.get("distinct_adjacent_donor_layouts", True),
            )
        ),
    )


_VARIANTS_CACHE: dict[str, dict] | None = None


def _load_variants_yaml() -> dict[str, dict]:
    global _VARIANTS_CACHE  # noqa: PLW0603
    if _VARIANTS_CACHE is not None:
        return _VARIANTS_CACHE
    from pathlib import Path

    try:
        import yaml

        path = Path(__file__).resolve().parents[3] / "configs" / "variants.default.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = data.get("variants") if isinstance(data, dict) else []
        _VARIANTS_CACHE = {
            str(entry.get("strategy") or entry.get("id")): entry
            for entry in (items or [])
            if isinstance(entry, dict) and (entry.get("strategy") or entry.get("id"))
        }
    except Exception:  # noqa: BLE001
        _VARIANTS_CACHE = {}
    return _VARIANTS_CACHE


def variant_axis_config(strategy: object) -> VariantAxisSettings:
    name = getattr(strategy, "value", str(strategy or "balanced"))
    entry = _load_variants_yaml().get(name) or {}
    if name == "faithful":
        return VariantAxisSettings(
            density_level=str(entry.get("density_level", "dense")),
            max_bullets_per_slide=int(entry.get("max_bullets_per_slide", 5)),
            max_words_per_bullet=int(entry.get("max_words_per_bullet", 12)),
            visual_archetype_target_share=float(entry.get("visual_archetype_target_share", 0.50)),
            max_bullets_archetype_share=float(entry.get("max_bullets_archetype_share", 0.3334)),
            closing_bullets=int(entry.get("closing_bullets", 3)),
            data_takeaway_bullets=int(entry.get("data_takeaway_bullets", 3)),
        )
    if name == "visual":
        return VariantAxisSettings(
            density_level=str(entry.get("density_level", "sparse")),
            max_bullets_per_slide=int(entry.get("max_bullets_per_slide", 3)),
            max_words_per_bullet=int(entry.get("max_words_per_bullet", 10)),
            visual_archetype_target_share=float(entry.get("visual_archetype_target_share", 0.90)),
            max_bullets_archetype_share=float(entry.get("max_bullets_archetype_share", 0.10)),
            closing_bullets=int(entry.get("closing_bullets", 1)),
            data_takeaway_bullets=int(entry.get("data_takeaway_bullets", 2)),
        )
    return VariantAxisSettings(
        density_level=str(entry.get("density_level", "medium")),
        max_bullets_per_slide=int(entry.get("max_bullets_per_slide", 4)),
        max_words_per_bullet=int(entry.get("max_words_per_bullet", 12)),
        visual_archetype_target_share=float(entry.get("visual_archetype_target_share", 0.75)),
        max_bullets_archetype_share=float(entry.get("max_bullets_archetype_share", 0.25)),
        closing_bullets=int(entry.get("closing_bullets", 2)),
        data_takeaway_bullets=int(entry.get("data_takeaway_bullets", 3)),
    )


def score_evidence_units(units: list, heading: str = "") -> float:
    """Оценка силы доказательств группы/слайда для ранжирования в пирамиде Минто / SCQA."""
    score = 0.0
    numeric_units = 0
    for u in units:
        kind = getattr(getattr(u, "kind", None), "value", getattr(u, "kind", ""))
        text = getattr(u, "text", None) or ""
        if kind == "chart":
            score += 12.0
        elif kind == "table":
            score += 10.0
        elif kind == "number":
            score += 6.0
        elif kind == "diagram":
            score += 5.0
        elif kind == "quote":
            score += 3.5
        if kind in ("bullet", "paragraph", "number", "label") and _HAS_DIGIT_RE.search(text):
            # Игнорируем чисто порядковые номера вида "Секция 1" или "пункт 2" без метрик
            if (
                re.search(
                    r"\d+\s*(%|₽|руб|млн|млрд|тыс|x|х|раз|ч|дн|мес|лет|год|шт|мс|сек|\$|€|[.,]\d)",
                    text,
                    re.I,
                )
                or len(re.findall(r"\d+", text)) >= 2
            ):
                numeric_units += 1
    score += min(numeric_units, 5) * 2.5
    if _HAS_DIGIT_RE.search(heading or "") and re.search(
        r"\d+\s*(%|₽|руб|млн|млрд|тыс|x|х|раз)", heading or "", re.I
    ):
        score += 2.0
    body_count = sum(
        1
        for u in units
        if getattr(getattr(u, "kind", None), "value", getattr(u, "kind", "")) != "title"
    )
    score += min(body_count, 5) * 0.15
    return round(score, 4)


def classify_scqa_stage(
    heading: str,
    units: list | None = None,
    purpose: object = None,
    idx: int = 0,
    has_explicit_situation: bool = False,
) -> str:
    """Классифицирует секцию или слайд в одну из стадий SCQA:
    situation -> complication -> answer -> evidence -> recommendation."""
    units = units or []
    p_val = getattr(purpose, "value", str(purpose or ""))
    h = (heading or "").strip()
    if p_val == "cta" or _SCQA_RECOMMENDATION_RE.search(h):
        return "recommendation"
    if p_val == "problem" or _SCQA_COMPLICATION_RE.search(h):
        return "complication"
    if p_val == "solution" or _SCQA_ANSWER_RE.search(h):
        return "answer"
    if _SCQA_SITUATION_RE.search(h):
        return "situation"
    has_structured_evidence = any(
        getattr(getattr(u, "kind", None), "value", getattr(u, "kind", ""))
        in ("chart", "table", "number", "diagram")
        for u in units
    )
    if (
        idx == 0
        and not has_explicit_situation
        and not has_structured_evidence
        and score_evidence_units(units, h) < 2.5
    ):
        return "situation"
    return "evidence"


def order_groups_scqa(groups: list[tuple]) -> list[tuple]:
    """Раскладывает группы (Section, list[ContentUnit]) по SCQA:
    situation -> complication -> answer -> evidence (по убыванию оценки) -> recommendation."""
    if len(groups) <= 1:
        return list(groups)
    has_explicit_sit = any(
        bool(_SCQA_SITUATION_RE.search(getattr(sec, "heading", "") or "")) for sec, _ in groups
    )
    stage_order = {stage: idx for idx, stage in enumerate(SCQA_STAGES)}
    decorated: list[tuple[int, float, int, tuple]] = []
    for i, (sec, units) in enumerate(groups):
        heading = getattr(sec, "heading", "") or ""
        stage = classify_scqa_stage(
            heading,
            units,
            purpose=None,
            idx=i,
            has_explicit_situation=has_explicit_sit,
        )
        ev_score = score_evidence_units(units, heading) if stage == "evidence" else 0.0
        decorated.append((stage_order[stage], -ev_score, i, (sec, units)))
    decorated.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in decorated]


_STEP_LABEL_RE = re.compile(
    r"^\s*(?:этап|шаг|фаза|пункт|часть|раздел|step|phase|stage)\s*\d+\b",
    re.I,
)


def _numeric_unit_count(units: list) -> int:
    count = 0
    for u in units:
        kind = getattr(getattr(u, "kind", None), "value", getattr(u, "kind", ""))
        if kind == "title":
            continue
        if kind == "number":
            count += 1
            continue
        text = getattr(u, "text", None) or ""
        cleaned = _STEP_LABEL_RE.sub("", text)
        if _HAS_DIGIT_RE.search(cleaned) and (
            re.search(
                r"\d+\s*(%|₽|руб|млн|млрд|тыс|x|х|раз|ч|дн|мес|лет|год|шт|мс|сек|\$|€|[.,]\d)",
                cleaned,
                re.I,
            )
            or len(re.findall(r"\d+", cleaned)) >= 1
        ):
            count += 1
    return count


def select_semantic_archetype(slide: object, strategy: object = None) -> str:
    """Выбирает семантический архетип слайда по типу факта и объёму контента:
    kpi_cards | big_number | chart_takeaway | comparison | table_topn |
    timeline | process | quote | callout | bullets."""
    units = list(getattr(slide, "content_units", None) or [])
    purpose_val = getattr(getattr(slide, "purpose", None), "value", "")
    title = getattr(slide, "title_intent", "") or ""
    body_units = [
        u
        for u in units
        if getattr(getattr(u, "kind", None), "value", getattr(u, "kind", "")) != "title"
    ]
    kinds = {getattr(getattr(u, "kind", None), "value", getattr(u, "kind", "")) for u in body_units}
    if "chart" in kinds or purpose_val == "data" and "table" not in kinds:
        if "chart" in kinds:
            return "chart_takeaway"
    if "table" in kinds:
        return "table_topn"
    if "quote" in kinds or purpose_val == "quote":
        return "quote"

    n_body = len(body_units)
    num_count = _numeric_unit_count(body_units)
    all_text = " ".join([title, *(getattr(u, "text", "") or "" for u in body_units)])
    strat_name = getattr(strategy, "value", str(strategy or "balanced"))

    if purpose_val == "comparison" or (n_body == 2 and _COMPARISON_RE.search(all_text)):
        return "comparison"
    if purpose_val == "timeline" or (_TIMELINE_RE.search(title) and 2 <= n_body <= 5):
        return "timeline"
    if purpose_val == "process" or (_PROCESS_RE.search(title) and 2 <= n_body <= 5):
        return "process"

    min_kpi_nums = (
        3 if strat_name == "faithful" and title not in ("Ключевые выводы", "Key findings") else 2
    )
    if num_count >= min_kpi_nums and 2 <= n_body <= 4:
        return "kpi_cards"
    if num_count == 1 and n_body == 1 and _HAS_DIGIT_RE.search(all_text):
        return "big_number"
    if _TIMELINE_RE.search(all_text) and 2 <= n_body <= 5:
        return "timeline"
    if _PROCESS_RE.search(all_text) and 2 <= n_body <= 5:
        return "process"
    if _QUOTE_RE.search(all_text) and n_body <= 2:
        return "quote"
    if n_body == 1 or (_CALLOUT_RE.search(title) and n_body <= 2):
        return "callout"

    if strat_name == "visual":
        if n_body == 2:
            return "comparison"
        if 3 <= n_body <= 4:
            return "kpi_cards" if num_count >= 1 else "process"
        if n_body >= 5:
            return "timeline"
        return "callout"
    if strat_name == "balanced":
        if n_body == 2 and (
            ":" in (getattr(body_units[0], "text", "") or "")
            or "—" in (getattr(body_units[0], "text", "") or "")
        ):
            return "comparison"
        if purpose_val in ("benefits", "solution") and 2 <= n_body <= 4:
            return "kpi_cards" if num_count >= 1 else "process"
        if purpose_val == "cta" and 2 <= n_body <= 5:
            return "process"
    return "bullets"


def slide_archetype(slide: object) -> str:
    """Возвращает назначенный семантический архетип слайда (или вычисляет его)."""
    purpose_val = getattr(getattr(slide, "purpose", None), "value", "")
    if purpose_val in ("title", "agenda", "section_divider", "thank_you", "qa"):
        return purpose_val
    units = list(getattr(slide, "content_units", None) or [])
    for u in units:
        kind = getattr(getattr(u, "kind", None), "value", getattr(u, "kind", ""))
        if kind == "title":
            continue
        role = getattr(u, "role", "") or ""
        if role in _ROLE_TO_ARCHETYPE and role != "bullet":
            return _ROLE_TO_ARCHETYPE[role]
    if any(
        getattr(getattr(u, "kind", None), "value", getattr(u, "kind", "")) == "chart" for u in units
    ):
        return "chart_takeaway"
    if any(
        getattr(getattr(u, "kind", None), "value", getattr(u, "kind", "")) == "table" for u in units
    ):
        return "table_topn"
    for u in units:
        kind = getattr(getattr(u, "kind", None), "value", getattr(u, "kind", ""))
        if kind != "title" and (getattr(u, "role", "") or "") == "bullet":
            # На «Ключевых выводах» даже при role="bullet" сохраняем kpi_cards
            if getattr(slide, "title_intent", "") in ("Ключевые выводы", "Key findings"):
                return "kpi_cards"
            return "bullets"
    return select_semantic_archetype(slide)


def _slide_chart_type(slide: object) -> str | None:
    for u in getattr(slide, "content_units", None) or []:
        kind = getattr(getattr(u, "kind", None), "value", getattr(u, "kind", ""))
        if kind == "chart":
            spec = getattr(u, "chart_spec", None)
            ct = getattr(spec, "chart_type", None) if spec else None
            return getattr(ct, "value", str(ct)) if ct else "chart"
    card = getattr(slide, "chart", None)
    if isinstance(card, dict) and card.get("type"):
        return str(card["type"])
    return None


def _apply_archetype_to_slide(
    slide: object, archetype: str, *, preserve_so_what_role: bool = False
) -> object:
    """Записывает семантический архетип в роли контентных юнитов слайда."""
    role = _ARCHETYPE_TO_ROLE.get(archetype, "bullet")
    units = list(getattr(slide, "content_units", None) or [])
    is_key_findings = getattr(slide, "title_intent", "") in ("Ключевые выводы", "Key findings")
    if is_key_findings and archetype == "kpi_cards":
        return slide
    new_units = []
    assigned_primary = False
    for u in units:
        kind = getattr(getattr(u, "kind", None), "value", getattr(u, "kind", ""))
        u_role = getattr(u, "role", "") or ""
        if (
            kind in ("title", "chart", "table", "image", "diagram", "icon")
            or u_role in ("equation", "eyebrow", "agenda-item", "subtitle")
            or getattr(u, "locked", False)
        ):
            new_units.append(u)
            continue
        if preserve_so_what_role and assigned_primary:
            new_units.append(u)
            continue
        new_units.append(u.model_copy(update={"role": role}))
        assigned_primary = True
    return slide.model_copy(update={"content_units": new_units})


def _truncate_words_with_overflow(text: str, max_words: int) -> tuple[str, str | None]:
    words = (text or "").split()
    if len(words) <= max_words:
        return text, None
    head = " ".join(words[:max_words]).rstrip(",;:—-")
    return head, text


def enforce_slide_density(
    slide: object,
    *,
    max_bullets: int = 5,
    max_words: int = 12,
    overflow_to_notes: bool = True,
    density_level: str | None = None,
) -> object:
    """Ограничивает плотность слайда: <= max_bullets пунктов по <= max_words слов;
    длинный текст и лишние пункты переносятся в speaker_note без потери evidence_ids."""
    from deckdna.contracts.deck_plan import DensityBudget, Kind, Level

    purpose_val = getattr(getattr(slide, "purpose", None), "value", "")
    if purpose_val in ("title", "agenda", "section_divider", "thank_you", "qa"):
        return slide
    units = list(getattr(slide, "content_units", None) or [])
    total_bullets_on_slide = sum(1 for u in units if getattr(u, "kind", None) == Kind.bullet)
    kept: list = []
    overflow_chunks: list[str] = []
    dropped_evidence: list[str] = []
    bullet_seen = 0

    for u in units:
        kind = getattr(u, "kind", None)
        u_role = getattr(u, "role", "") or ""
        text = (getattr(u, "text", None) or "").strip()
        if (
            kind in (Kind.title, Kind.chart, Kind.table, Kind.image, Kind.diagram, Kind.icon)
            or u_role in ("equation", "eyebrow", "agenda-item")
            or getattr(u, "locked", False)
        ):
            kept.append(u)
            continue
        if kind == Kind.bullet:
            bullet_seen += 1
            if bullet_seen > max_bullets:
                if text:
                    overflow_chunks.append(text)
                for eid in getattr(u, "evidence_ids", None) or []:
                    if eid not in dropped_evidence:
                        dropped_evidence.append(eid)
                continue
            words = text.split()
            # Одиночный callout-тезис на слайде с 1-2 однобуквенными предлогами (до 95 симв.)
            is_single_short_callout = (
                total_bullets_on_slide == 1 and len(text) <= 95 and len(words) <= max_words + 1
            )
            if text and len(words) > max_words and not is_single_short_callout:
                short_text, full_text = _truncate_words_with_overflow(text, max_words)
                if full_text:
                    overflow_chunks.append(full_text)
                u = u.model_copy(update={"text": short_text})
            kept.append(u)
        else:
            kept.append(u)

    if dropped_evidence and kept:
        last = kept[-1]
        merged_ev = list(dict.fromkeys([*(last.evidence_ids or []), *dropped_evidence]))
        kept[-1] = last.model_copy(update={"evidence_ids": merged_ev})

    note = getattr(slide, "speaker_note", None) or ""
    if overflow_to_notes and overflow_chunks:
        extra = " ".join(overflow_chunks)
        note = f"{note} {extra}".strip() if note else extra

    non_title_count = sum(1 for u in kept if getattr(u, "kind", None) != Kind.title)
    if density_level in ("sparse", "medium", "dense"):
        lvl = Level(density_level)
    else:
        lvl = (
            Level.sparse
            if non_title_count <= 2
            else Level.medium
            if non_title_count <= 4
            else Level.dense
        )
    budget = DensityBudget(
        level=lvl,
        max_items=min(max(non_title_count, 1), max_bullets),
        max_chars=max_bullets * max_words * 8,
    )
    updates: dict = {"content_units": kept, "density_budget": budget}
    if note:
        updates["speaker_note"] = note
    return slide.model_copy(update=updates)


def _alternative_archetypes(
    slide: object,
    *,
    forbidden: set[str],
    allow_bullets: bool,
    prefer_unseen: set[str],
) -> str:
    """Подбирает альтернативный архетип для устранения повтора или дефицита разнообразия."""
    units = [
        u
        for u in (getattr(slide, "content_units", None) or [])
        if getattr(getattr(u, "kind", None), "value", getattr(u, "kind", "")) != "title"
    ]
    n_body = len(units)
    num_count = _numeric_unit_count(units)
    candidates: list[str] = []
    if num_count >= 2 and 2 <= n_body <= 5:
        candidates.append("kpi_cards")
    if num_count >= 1:
        candidates.append("big_number")
    if n_body >= 2:
        candidates.extend(["comparison", "process", "timeline", "kpi_cards"])
    candidates.extend(["callout", "quote", "big_number", "process", "comparison", "timeline"])
    if allow_bullets:
        candidates.append("bullets")

    # Сначала ищем кандидата, которого ещё не было в колоде и который не запрещён
    for cand in candidates:
        if (
            cand not in forbidden
            and cand not in prefer_unseen
            and (allow_bullets or cand != "bullets")
        ):
            return cand
    for cand in candidates:
        if cand not in forbidden and (allow_bullets or cand != "bullets"):
            return cand
    return "callout" if "callout" not in forbidden else "comparison"


def enforce_archetype_diversity(
    slides: list,
    *,
    strategy: object = None,
    settings: StorylineSettings | None = None,
) -> list:
    """Обеспечивает ограничения разнообразия архетипов:
    1) один архетип не два раза подряд (кроме chart_takeaway с разными типами графиков);
    2) доля bullets <= min(settings.max_bullets_archetype_share, axis.max_bullets_archetype_share);
    3) в колоде >= 8 слайдов — минимум 4 разных архетипа."""
    cfg = settings or StorylineSettings()
    axis = variant_axis_config(strategy)
    structural = {"title", "agenda", "section_divider", "thank_you", "qa"}

    out = list(slides)
    content_indices = [
        i
        for i, s in enumerate(out)
        if getattr(getattr(s, "purpose", None), "value", "") not in structural
    ]
    if not content_indices:
        return out

    archetypes: dict[int, str] = {}
    for idx in content_indices:
        archetypes[idx] = select_semantic_archetype(out[idx], strategy=strategy)

    strat_name = getattr(strategy, "value", str(strategy or "balanced"))
    max_bullets_share = min(cfg.max_bullets_archetype_share, axis.max_bullets_archetype_share)
    max_bullets_count = max(0, int(math.floor(len(out) * max_bullets_share)))
    if strat_name == "visual":
        max_bullets_count = 0
    elif strat_name == "balanced":
        max_bullets_count = min(max_bullets_count, 1)

    # Шаг 1: ограничиваем долю bullets
    bullet_slides = [idx for idx in content_indices if archetypes[idx] == "bullets"]
    if len(bullet_slides) > max_bullets_count:
        used_so_far = set(archetypes.values())
        # Оставляем в bullets только первые max_bullets_count (не подряд), остальные — в визуальные
        kept_bullets: list[int] = []
        to_upgrade: list[int] = []
        for idx in bullet_slides:
            if len(kept_bullets) < max_bullets_count and (
                not kept_bullets
                or content_indices.index(idx) - content_indices.index(kept_bullets[-1]) > 1
            ):
                kept_bullets.append(idx)
            else:
                to_upgrade.append(idx)
        cycle = ["comparison", "process", "timeline", "callout", "kpi_cards", "big_number", "quote"]
        for pos, idx in enumerate(to_upgrade):
            prev_arch = archetypes.get(idx - 1)
            next_arch = archetypes.get(idx + 1)
            forbidden = {a for a in (prev_arch, next_arch) if a}
            rotated = cycle[pos % len(cycle) :] + cycle[: pos % len(cycle)]
            chosen = next(
                (c for c in rotated if c not in forbidden and c not in used_so_far),
                next((c for c in rotated if c not in forbidden), "comparison"),
            )
            archetypes[idx] = chosen
            used_so_far.add(chosen)

    # Шаг 2: запрет двух одинаковых архетипов подряд (кроме chart_takeaway с разными типами)
    if cfg.no_adjacent_same_archetype:
        used_so_far = set(archetypes.values())
        for pos, idx in enumerate(content_indices):
            if pos == 0:
                continue
            prev_idx = content_indices[pos - 1]
            # Если между ними был section_divider, проверяем и непосредственного соседа по контенту
            prev_arch = archetypes[prev_idx]
            curr_arch = archetypes[idx]
            if curr_arch != prev_arch:
                continue
            if (
                curr_arch == "chart_takeaway"
                and cfg.allow_adjacent_chart_if_different_type
                and _slide_chart_type(out[prev_idx]) != _slide_chart_type(out[idx])
            ):
                continue
            next_arch = (
                archetypes[content_indices[pos + 1]] if pos + 1 < len(content_indices) else None
            )
            current_bullets = sum(1 for a in archetypes.values() if a == "bullets")
            allow_b = current_bullets < max_bullets_count
            alt = _alternative_archetypes(
                out[idx],
                forbidden={prev_arch, *([next_arch] if next_arch else [])},
                allow_bullets=allow_b,
                prefer_unseen=used_so_far,
            )
            archetypes[idx] = alt
            used_so_far.add(alt)

    # Шаг 3: в колоде >= 8 слайдов — минимум 4 разных архетипа
    distinct = {archetypes[idx] for idx in content_indices}
    if len(out) >= 8 and len(distinct) < cfg.min_distinct_archetypes_for_8_plus:
        needed = cfg.min_distinct_archetypes_for_8_plus - len(distinct)
        pool_order = [
            "kpi_cards",
            "comparison",
            "process",
            "timeline",
            "callout",
            "big_number",
            "quote",
            "table_topn",
        ]
        # Выбираем слайды для обогащения разнообразия (не ломая chart_takeaway и table_topn)
        modifiable = [
            idx
            for idx in reversed(content_indices)
            if archetypes[idx] not in ("chart_takeaway", "table_topn")
        ] or list(reversed(content_indices))
        for idx in modifiable:
            if needed <= 0:
                break
            pos = content_indices.index(idx)
            prev_a = archetypes[content_indices[pos - 1]] if pos > 0 else None
            next_a = (
                archetypes[content_indices[pos + 1]] if pos + 1 < len(content_indices) else None
            )
            cand = next(
                (a for a in pool_order if a not in distinct and a not in {prev_a, next_a}),
                None,
            )
            if cand is not None:
                archetypes[idx] = cand
                distinct.add(cand)
                needed = cfg.min_distinct_archetypes_for_8_plus - len(distinct)

    # Применяем роли архетипов к слайдам
    for idx in content_indices:
        is_data = getattr(getattr(out[idx], "purpose", None), "value", "") == "data"
        out[idx] = _apply_archetype_to_slide(
            out[idx],
            archetypes[idx],
            preserve_so_what_role=is_data,
        )
    return out


def _diversify_adjacent_purposes(slides: list) -> list:
    """Разводит одинаковые Purpose.overview / Purpose.custom на соседних слайдах,
    чтобы компилятор выбирал разные донорские макетов (layout_fit / rerank)."""
    from deckdna.contracts.deck_plan import Purpose

    archetype_purpose_map = {
        "kpi_cards": Purpose.benefits,
        "big_number": Purpose.quote,
        "comparison": Purpose.comparison,
        "timeline": Purpose.timeline,
        "process": Purpose.process,
        "quote": Purpose.quote,
        "callout": Purpose.solution,
        "table_topn": Purpose.comparison,
    }
    cycle_purposes = (
        Purpose.benefits,
        Purpose.comparison,
        Purpose.process,
        Purpose.solution,
        Purpose.timeline,
        Purpose.quote,
    )
    out = list(slides)
    for i in range(1, len(out)):
        curr = out[i]
        prev = out[i - 1]
        if curr.purpose == Purpose.custom:
            arch = slide_archetype(curr)
            mapped = archetype_purpose_map.get(arch, cycle_purposes[i % len(cycle_purposes)])
            if mapped == prev.purpose:
                mapped = cycle_purposes[(i + 1) % len(cycle_purposes)]
            out[i] = curr.model_copy(update={"purpose": mapped})
        elif curr.purpose == Purpose.overview and prev.purpose == Purpose.overview:
            arch = slide_archetype(curr)
            mapped = archetype_purpose_map.get(arch, cycle_purposes[i % len(cycle_purposes)])
            if mapped == prev.purpose:
                mapped = Purpose.comparison
            out[i] = curr.model_copy(update={"purpose": mapped})
        elif curr.purpose == Purpose.cta and prev.purpose == Purpose.cta:
            out[i - 1] = prev.model_copy(update={"purpose": Purpose.benefits})
    return out


def should_use_closing_recommendation(pack: object, brief: object, slides: list) -> bool:
    """Финальный слайд-рекомендация (Purpose.cta) вместо «Спасибо» для всех
    содержательных и дата-колод; одиночная минимальная секция без данных и CTA
    остаётся thank_you для обратной совместимости минимальных фикстур."""
    datasets = getattr(pack, "datasets", None) or []
    charts = getattr(pack, "charts", None) or []
    tables = getattr(pack, "tables", None) or []
    insights = getattr(pack, "insights", None) or []
    sections = getattr(pack, "sections", None) or []
    if getattr(brief, "explain_slide", False) and not any(
        getattr(i, "implication", None) and getattr(i, "confirmed", False) for i in insights
    ):
        return False
    if datasets or charts or tables or insights:
        return True
    if getattr(brief, "call_to_action", None):
        return True
    if len(sections) >= 2:
        return True
    # Для 1 секции проверяем наличие метрик/графиков/таблиц
    for s in slides:
        for u in getattr(s, "content_units", None) or []:
            kind = getattr(getattr(u, "kind", None), "value", getattr(u, "kind", ""))
            if kind in ("chart", "table", "number", "diagram"):
                return True
    return False


def build_closing_recommendation_slide(
    pack: object,
    brief: object,
    slides: list,
    *,
    strategy: object = None,
    lang: str = "ru",
) -> object:
    """Строит финальный слайд-вывод с рекомендацией из данных/контента (Purpose.cta)."""
    from deckdna.contracts.deck_plan import (
        ContentUnit,
        DensityBudget,
        Kind,
        Level,
        Purpose,
        SlidePlan,
    )

    ru = (lang or "ru").lower().startswith("ru")
    axis = variant_axis_config(strategy)
    has_datasets = bool(getattr(pack, "datasets", None))
    target_bullets = 3 if has_datasets else axis.closing_bullets
    cover_title = (getattr(slides[0], "title_intent", "") if slides else "").strip()

    bullets: list[tuple[str, list[str]]] = []
    insights = list(getattr(pack, "insights", None) or [])
    pack_id = getattr(pack, "id", "pack")

    if getattr(brief, "call_to_action", None):
        cta_text = str(brief.call_to_action).strip()
        if cta_text:
            bullets.append((cta_text, [f"{pack_id}:meta"]))

    # 1. Рекомендации из инсайтов данных (по убыванию силы)
    strat_name = getattr(strategy, "value", str(strategy or "balanced"))
    if strat_name == "faithful":
        action_prefix_ru = {
            "growth": "Закрепить текущие драйверы и масштабировать темп роста показателя",
            "acceleration": "Масштабировать ведущие каналы с ускоряющейся положительной динамикой",
            "leader": "Сфокусировать приоритетные ресурсы на лидирующем сегменте и обновить план",
            "concentration": "Защитить ключевые сегменты и снизить операционный риск концентрации",
            "gap": "Сократить структурный разрыв между лидирующим и отстающими сегментами",
            "decline": "Детально разобрать факторы и остановить снижение ключевого показателя",
            "laggard": "Пересмотреть операционную стратегию и условия работы по отстающим",
            "correlation": (
                "Использовать выявленную статистическую связь показателей при планировании бюджета"
            ),
            "spread": "Формировать операционный план от типичного медианного уровня значений",
            "wide_spread": "Заложить целевой резерв под широкий диапазон колебаний показателя",
            "flat": "Перераспределить инвестиционные приоритеты внутри стабильного среза данных",
        }
        default_prefix_ru = "Учесть в операционном плане развития"
    elif strat_name == "visual":
        action_prefix_ru = {
            "growth": "Удержать рост",
            "acceleration": "Усилить ускорение",
            "leader": "Фокус на лидере",
            "concentration": "Снизить концентрацию",
            "gap": "Сократить разрыв",
            "decline": "Остановить спад",
            "laggard": "Обновить хвост",
            "correlation": "Проверить связь",
            "spread": "Опора на медиану",
            "wide_spread": "Резерв под разброс",
            "flat": "Сменить приоритеты",
        }
        default_prefix_ru = "В план"
    else:
        action_prefix_ru = {
            "growth": "Закрепить драйверы роста",
            "acceleration": "Масштабировать ускорение",
            "leader": "Сфокусировать ресурсы на лидере",
            "concentration": "Защитить ключевые сегменты",
            "gap": "Сократить разрыв между сегментами",
            "decline": "Остановить снижение показателя",
            "laggard": "Пересмотреть стратегию по отстающим",
            "correlation": "Использовать выявленную связь в планировании",
            "spread": "Планировать от типичного уровня",
            "wide_spread": "Заложить резерв под разброс значений",
            "flat": "Перераспределить приоритеты внутри среза",
        }
        default_prefix_ru = "Учесть в плане"

    sorted_insights = sorted(
        [i for i in insights if getattr(i, "fact_type", "") != "missing"],
        key=lambda x: getattr(x, "strength", 0.0),
        reverse=True,
    )
    max_words_cta = 12 if has_datasets else axis.max_words_per_bullet
    for ins in sorted_insights:
        if len(bullets) >= 4:
            break
        ev_id = f"{pack_id}:{ins.id}"
        ft = getattr(ins, "fact_type", "") or "share"
        imp = getattr(ins, "implication", None)
        headline = (getattr(ins, "headline", None) or getattr(ins, "fact", None) or "").strip()
        if imp:
            text = imp
        elif headline and ru:
            prefix = action_prefix_ru.get(ft, default_prefix_ru)
            text = (
                f"{prefix}: {headline[0].lower() + headline[1:]}" if len(headline) > 1 else headline
            )
        else:
            text = headline
        if text:
            words = text.split()
            if len(words) > max_words_cta and not has_datasets:
                text = " ".join(words[:max_words_cta]).rstrip(",;:—-")
            if all(text != b[0] for b in bullets):
                bullets.append((text, [ev_id]))

    # 2. Если инсайтов нет — рекомендации из финальных/рекомендательных секций пака
    sections = list(getattr(pack, "sections", None) or [])
    if not bullets and sections:
        for sec in reversed(sections):
            h_raw = (getattr(sec, "heading", "") or "").strip()
            if classify_scqa_stage(h_raw) == "recommendation":
                for bi, blk in enumerate(getattr(sec, "blocks", None) or []):
                    blk_kind = getattr(
                        getattr(blk, "kind", None), "value", getattr(blk, "kind", "")
                    )
                    if blk_kind == "list":
                        for item in getattr(blk, "items", None) or []:
                            txt = item.strip()
                            if txt:
                                words = txt.split()
                                if len(words) > axis.max_words_per_bullet:
                                    txt = " ".join(words[: axis.max_words_per_bullet]).rstrip(
                                        ",;:—-"
                                    )
                                bullets.append((txt, [f"{pack_id}:{sec.id}:b{bi}"]))
                    elif blk_kind == "paragraph" and getattr(blk, "text", None):
                        txt = blk.text.strip()
                        words = txt.split()
                        if len(words) > axis.max_words_per_bullet:
                            txt = " ".join(words[: axis.max_words_per_bullet]).rstrip(",;:—-")
                        bullets.append((txt, [f"{pack_id}:{sec.id}:b{bi}"]))

    action_templates_ru = (
        "Приоритет — {h}: закрепить целевые метрики и ответственных",
        "Масштабировать «{h}» с контролем качества на каждом этапе",
        "Зафиксировать дорожную карту и контрольные точки по блоку «{h}»",
        "Перераспределить ресурсы в пользу направления «{h}»",
    )
    action_templates_en = (
        "Prioritize {h} with clear target metrics and owners",
        "Scale {h} with stage-gate quality controls",
        "Lock roadmap milestones for {h}",
        "Align resources around {h} execution",
    )
    templates = action_templates_ru if ru else action_templates_en
    if not has_datasets:
        for idx_sec, sec in enumerate(reversed(sections)):
            if len(bullets) >= 4:
                break
            h = re.sub(r"^\d+[\.\)]\s*", "", (getattr(sec, "heading", "") or "").strip()).strip()
            if not h or h.casefold() == cover_title.casefold():
                continue
            if classify_scqa_stage(h) == "recommendation":
                continue
            short_h = " ".join(h.split()[:4]).rstrip(".:,;—-")
            text = templates[idx_sec % len(templates)].format(h=short_h)
            words = text.split()
            if len(words) > axis.max_words_per_bullet:
                text = " ".join(words[: axis.max_words_per_bullet]).rstrip(",;:—-")
            sec_blocks = getattr(sec, "blocks", None) or []
            ev_id = f"{pack_id}:{sec.id}:b0" if sec_blocks else f"{pack_id}:{sec.id}"
            if all(text != b[0] for b in bullets):
                bullets.append((text, [ev_id]))

    if not bullets:
        fallback_text = (
            "Зафиксировать целевые показатели и перейти к внедрению"
            if ru
            else "Lock target metrics and proceed to implementation"
        )
        bullets.append((fallback_text, [f"{pack_id}:meta"]))

    kept_bullets = bullets[:target_bullets]
    overflow_bullets = bullets[target_bullets:]

    existing_titles = {getattr(s, "title_intent", "") for s in slides[:-1]}
    base_title = "Рекомендации и следующие шаги" if ru else "Recommendations and next steps"
    if base_title in existing_titles or base_title.casefold() == cover_title.casefold():
        base_title = "Что предлагаем по итогам анализа" if ru else "Recommended action plan"

    all_ev = list(dict.fromkeys(eid for _, evs in bullets for eid in evs)) or [f"{pack_id}:meta"]
    role = "step" if len(kept_bullets) >= 2 else "callout"
    units = [
        ContentUnit(
            role="title",
            kind=Kind.title,
            text=base_title,
            evidence_ids=all_ev[:1],
        ),
        *(
            ContentUnit(
                role=role,
                kind=Kind.bullet,
                text=b_text,
                evidence_ids=b_ev,
            )
            for b_text, b_ev in kept_bullets
        ),
    ]
    if units and len(units) > 1:
        merged_last_ev = list(dict.fromkeys([*(units[-1].evidence_ids or []), *all_ev]))
        units[-1] = units[-1].model_copy(update={"evidence_ids": merged_last_ev})

    note_parts = [text for text, _ in overflow_bullets]
    speaker_note = (
        " ".join(note_parts)
        if note_parts
        else (
            "Итоговые рекомендации сформированы на основе ключевых фактов колоды."
            if ru
            else "Final recommendations synthesized from the deck's key findings."
        )
    )
    slide_idx = len(slides) - 1 if slides else 0
    slide_id = getattr(slides[-1], "id", f"slide-{slide_idx}") if slides else f"slide-{slide_idx}"
    return SlidePlan(
        id=slide_id,
        index=slide_idx,
        purpose=Purpose.cta,
        title_intent=base_title,
        key_message=kept_bullets[0][0],
        evidence_ids=all_ev,
        content_units=units,
        density_budget=DensityBudget(
            level=Level(axis.density_level),
            max_items=len(kept_bullets),
            max_chars=len(kept_bullets) * axis.max_words_per_bullet * 8,
        ),
        speaker_note=speaker_note,
    )


def plan_archetypes(plan: object) -> list[str]:
    """Возвращает последовательность семантических архетипов по всем слайдам плана."""
    return [slide_archetype(s) for s in getattr(plan, "slides", [])]


def visual_archetype_share(plan: object) -> float:
    """Доля визуальных архетипов среди контентных слайдов колоды."""
    structural = {"title", "agenda", "section_divider", "thank_you", "qa"}
    content_archs = [
        slide_archetype(s)
        for s in getattr(plan, "slides", [])
        if slide_archetype(s) not in structural
    ]
    if not content_archs:
        return 0.0
    visual_count = sum(1 for a in content_archs if a in VISUAL_ARCHETYPES)
    return visual_count / len(content_archs)


def deck_text_density(plan: object) -> dict[str, float | int]:
    """Метрики текстовой плотности колоды (число буллетов и слов на контентных слайдах)."""
    structural = {"title", "agenda", "section_divider", "thank_you", "qa"}
    slides = [
        s
        for s in getattr(plan, "slides", [])
        if getattr(getattr(s, "purpose", None), "value", "") not in structural
    ]
    total_bullets = 0
    total_words = 0
    for s in slides:
        for u in getattr(s, "content_units", None) or []:
            kind = getattr(getattr(u, "kind", None), "value", getattr(u, "kind", ""))
            if kind == "title":
                continue
            text = (getattr(u, "text", None) or "").strip()
            if kind == "bullet":
                total_bullets += 1
            if text:
                total_words += len(text.split())
    n = max(len(slides), 1)
    return {
        "total_bullets": total_bullets,
        "total_words": total_words,
        "avg_bullets_per_slide": round(total_bullets / n, 3),
        "avg_words_per_slide": round(total_words / n, 3),
    }


def apply_storyline_and_grammar(
    plan: object,
    pack: object,
    brief: object,
    cfg: object | None = None,
    strategy: object = None,
) -> object:
    """Пост-обработка DeckPlan:
    1) Финальный слайд-вывод с рекомендацией из данных (Purpose.cta вместо thank_you);
    2) Ограничение плотности (<=5 буллетов по <=12 слов, плюс ось вариантов);
    3) Грамматика семантических архетипов и ограничения разнообразия;
    4) Разведение Purpose соседних однотипных слайдов для разных донорских макетов."""

    settings = storyline_config(cfg)
    axis = variant_axis_config(strategy)
    slides = list(getattr(plan, "slides", None) or [])
    if not slides:
        return plan

    lang = getattr(plan, "language", None) or getattr(brief, "language", "ru") or "ru"
    has_datasets = bool(getattr(pack, "datasets", None))

    # 1. Финальный слайд: рекомендация из данных/контента вместо «Спасибо»
    if settings.closing_recommendation and should_use_closing_recommendation(pack, brief, slides):
        last = slides[-1]
        last_purpose = getattr(getattr(last, "purpose", None), "value", "")
        last_units = getattr(last, "content_units", None) or []
        has_body = any(
            getattr(getattr(u, "kind", None), "value", getattr(u, "kind", "")) != "title"
            for u in last_units
        )
        if last_purpose == "thank_you" or (last_purpose == "cta" and not has_body):
            slides[-1] = build_closing_recommendation_slide(
                pack, brief, slides, strategy=strategy, lang=lang
            )
        elif last_purpose == "cta" and has_body:
            cta_max_b = (
                settings.max_bullets_per_slide
                if has_datasets
                else min(settings.max_bullets_per_slide, axis.closing_bullets)
            )
            cta_max_w = (
                settings.max_words_per_bullet
                if has_datasets
                else min(settings.max_words_per_bullet, axis.max_words_per_bullet)
            )
            slides[-1] = enforce_slide_density(
                last,
                max_bullets=cta_max_b,
                max_words=cta_max_w,
                overflow_to_notes=settings.overflow_to_speaker_notes,
                density_level=axis.density_level,
            )

    # 2. Плотность по слайдам (<= 5 пунктов по <= 12 слов + ось стратегии)
    # Для дата-паков (has_datasets) инвариант §6 выжимки: все три варианта
    # сохраняют идентичный набор фактов и чисел, меняются композиция, акценты и плотность.
    max_b = (
        settings.max_bullets_per_slide
        if has_datasets
        else min(settings.max_bullets_per_slide, axis.max_bullets_per_slide)
    )
    max_w = (
        settings.max_words_per_bullet
        if has_datasets
        else min(settings.max_words_per_bullet, axis.max_words_per_bullet)
    )
    for i, s in enumerate(slides):
        p_val = getattr(getattr(s, "purpose", None), "value", "")
        if p_val in ("title", "agenda", "section_divider", "thank_you", "qa"):
            continue
        if i == len(slides) - 1 and p_val == "cta":
            slide_max_b = max_b if has_datasets else min(max_b, axis.closing_bullets)
        elif p_val == "data":
            slide_max_b = max_b
        else:
            slide_max_b = max_b
        slides[i] = enforce_slide_density(
            s,
            max_bullets=slide_max_b,
            max_words=max_w,
            overflow_to_notes=settings.overflow_to_speaker_notes,
            density_level=axis.density_level,
        )

    # 3. Семантическая грамматика архетипов и ограничения разнообразия
    slides = enforce_archetype_diversity(slides, strategy=strategy, settings=settings)

    # 4. Разведение донорских макетов для соседних однотипных слайдов
    if settings.distinct_adjacent_donor_layouts:
        slides = _diversify_adjacent_purposes(slides)

    # Переиндексация на случай изменений
    slides = [
        s.model_copy(update={"index": idx}) if s.index != idx else s for idx, s in enumerate(slides)
    ]
    return plan.model_copy(update={"slides": slides})


# ---------------------------------------------------------------------------
# H. Story Director поверх FactLedger: контракт LLM, beam search (k=8),
#    длина по материалу, must_not_assume_direction, 3 варианта и выбор макета
# ---------------------------------------------------------------------------

ALLOWED_TRANSITIONS: frozenset[str] = frozenset(
    {
        "none",
        "context",
        "elaboration",
        "contrast",
        "drill_down",
        "temporal",
        "temporal_next",
        "exception",
        "",
    }
)

FORBIDDEN_CAUSAL_TRANSITIONS: frozenset[str] = frozenset(
    {
        "cause",
        "causal",
        "because",
        "effect",
        "consequence",
        "therefore",
        "причина",
        "следствие",
        "потому_что",
        "вызвано",
    }
)

ALLOWED_STORY_ROLES: frozenset[str] = frozenset(
    {
        "title",
        "answer",
        "context",
        "evidence",
        "contrast",
        "limitation",
        "recommendation",
        "overview",
        "cta",
        "summary",
    }
)

VARIANT_LAYOUT_PROFILES: dict[str, dict[str, Any]] = {
    "editorial": {
        "strategy": "faithful",
        "variant_name": "editorial",
        "density_level": "dense",
        "chart_layout_intent": "editorial",
        "kpi_layout_intent": "kpi_cards",
        "accent_style": "primary_focus",
    },
    "faithful": {
        "strategy": "faithful",
        "variant_name": "editorial",
        "density_level": "dense",
        "chart_layout_intent": "editorial",
        "kpi_layout_intent": "kpi_cards",
        "accent_style": "primary_focus",
    },
    "comparison": {
        "strategy": "balanced",
        "variant_name": "comparison",
        "density_level": "medium",
        "chart_layout_intent": "comparison",
        "kpi_layout_intent": "comparison",
        "accent_style": "comparative_panels",
    },
    "balanced": {
        "strategy": "balanced",
        "variant_name": "comparison",
        "density_level": "medium",
        "chart_layout_intent": "comparison",
        "kpi_layout_intent": "comparison",
        "accent_style": "comparative_panels",
    },
    "dashboard": {
        "strategy": "visual",
        "variant_name": "dashboard",
        "density_level": "sparse",
        "chart_layout_intent": "dashboard",
        "kpi_layout_intent": "big_number",
        "accent_style": "kpi_strip",
    },
    "visual": {
        "strategy": "visual",
        "variant_name": "dashboard",
        "density_level": "sparse",
        "chart_layout_intent": "dashboard",
        "kpi_layout_intent": "big_number",
        "accent_style": "kpi_strip",
    },
}

_DIRECTIONAL_GROWTH_RE = re.compile(
    r"\b(?:покаж\w*|докаж\w*|обосну\w*|подтверд\w*|продемонстрир\w*|show|prove|demonstrate)?\s*"
    r"(?:рост\w*|увеличен\w*|прирост\w*|подъ[её]м\w*|ускорен\w*|успех\w*|growth|increase|rise)\b",
    re.IGNORECASE,
)
_DIRECTIONAL_DECLINE_RE = re.compile(
    r"\b(?:покаж\w*|докаж\w*|обосну\w*|подтверд\w*|продемонстрир\w*|show|prove|demonstrate)?\s*"
    r"(?:паден\w*|снижен\w*|спад\w*|просадк\w*|сокращен\w*|убыт\w*|decline|drop|decrease|fall)\b",
    re.IGNORECASE,
)
_CAUSAL_CLAIM_TEXT_RE = re.compile(
    r"\b(?:потому\s+что|вследствие|из-за\s+этого|привело\s+к|вызвало|стало\s+причиной|является\s+причиной)\b",
    re.IGNORECASE,
)


class StoryLedgerSlide(_BaseModel):
    """Описание одного слайда в плане Story Director поверх FactLedger."""

    model_config = _ConfigDict(extra="forbid", strict=True)

    role: Literal["answer", "context", "evidence", "contrast", "limitation"]
    fact_ids: list[str] = _Field(default_factory=list)
    title_realization_id: str = ""
    transition: Literal["none", "context", "elaboration", "contrast", "drill_down", "temporal"] = (
        "none"
    )
    layout_intent: Literal[
        "editorial",
        "comparison",
        "dashboard",
        "kpi_cards",
        "big_number",
        "chart_takeaway",
        "table_topn",
        "callout",
    ] = "editorial"


class StoryLedgerPlan(_BaseModel):
    """Контракт ответа Story Director поверх FactLedger (§5 выжимки)."""

    model_config = _ConfigDict(extra="forbid", strict=True)

    main_fact_id: str = ""
    slides: list[StoryLedgerSlide] = _Field(default_factory=list)
    omitted_fact_ids: list[str] = _Field(default_factory=list)


@dataclass(frozen=True)
class StoryLedgerValidation:
    """Результат серверной проверки плана Story Director."""

    ok: bool
    reasons: list[str]
    plan: StoryLedgerPlan | None = None


def neutralize_goal_direction(purpose: str | None) -> dict[str, Any]:
    """Превращает направленную формулировку цели («показать рост продаж»)
    в нейтральный аналитический вопрос («Динамика и изменение показателя»)
    с флагом ``must_not_assume_direction=True``."""
    raw = (purpose or "").strip()
    assumed_direction: str | None = None
    if _DIRECTIONAL_GROWTH_RE.search(raw):
        assumed_direction = "increase"
    elif _DIRECTIONAL_DECLINE_RE.search(raw):
        assumed_direction = "decrease"

    neutral = raw
    neutral = re.sub(
        r"\b(?:покажите|показать|докажите|доказать|обоснуйте|обосновать|подтвердите|подтвердить)\s+"
        r"(?:рост|увеличение|прирост|снижение|падение|спад)\b",
        "Оценить динамику",
        neutral,
        flags=re.IGNORECASE,
    )
    neutral = re.sub(
        r"^(?:рост|увеличение|прирост|снижение|падение|спад)\s+",
        "Динамика ",
        neutral,
        flags=re.IGNORECASE,
    ).strip()
    neutral = re.sub(
        r"\b(?:show|prove|demonstrate)\s+(?:growth|increase|rise|decline|drop|decrease|fall)\b",
        "Assess change",
        neutral,
        flags=re.IGNORECASE,
    )
    if assumed_direction and neutral == raw:
        neutral = re.sub(
            r"рост\w*|увеличен\w*|прирост\w*|паден\w*|снижен\w*|спад\w*|growth|increase|decline|decrease",
            "изменение",
            neutral,
            flags=re.IGNORECASE,
        )
    if not neutral:
        neutral = "Что можно установить по данным"
    neutral = neutral.rstrip(".!? ") + "?"
    return {
        "raw_goal": raw,
        "neutral_question": neutral,
        "assumed_direction": assumed_direction,
        "must_not_assume_direction": True,
    }


def _is_substantive_fact(fact: object) -> bool:
    """Отличает содержательные факты о метриках от оговорок о пропусках/неполноте."""
    kind = (getattr(fact, "kind", "") or "").strip().casefold()
    pred = (getattr(fact, "predicate", "") or "").strip().casefold()
    return kind not in ("missing", "missing_data", "incomplete_period") and pred not in (
        "missing",
        "missing_data",
        "incomplete_period",
    )


def _ordered_fact_ids(ledger: object) -> list[str]:
    by_id: dict[str, Any] = getattr(ledger, "by_id", {}) or {}
    raw_ranked = list(getattr(ledger, "ranked", []) or [])
    ranked_ids = [item.id if hasattr(item, "id") else str(item) for item in raw_ranked]
    return list(dict.fromkeys([*ranked_ids, *by_id.keys()]))


def build_fact_ledger_catalog(ledger: object, brief: object | None = None) -> dict[str, Any]:
    """Строит компактный каталог фактов из FactLedger для Story Director.
    Сырые таблицы в промпт не передаются; модель видит только id, метаданные среза,
    предикат, оценку и допустимые шаблоны заголовков allowed_title_ids."""
    purpose = getattr(brief, "purpose", None) if brief is not None else None
    goal_info = neutralize_goal_direction(purpose)
    by_id: dict[str, Any] = getattr(ledger, "by_id", {}) or {}
    ordered_ids = _ordered_fact_ids(ledger)

    catalog_facts: list[dict[str, Any]] = []
    for fid in ordered_ids:
        fact = by_id.get(fid)
        if fact is None:
            continue
        catalog_facts.append(
            {
                "id": fact.id,
                "dataset_id": fact.dataset_id,
                "view_id": getattr(fact, "view_id", None),
                "metric": fact.metric,
                "aggregation": fact.aggregation,
                "scope": dict(getattr(fact, "scope", {}) or {}),
                "period": getattr(fact, "period", ""),
                "kind": fact.kind,
                "predicate": fact.predicate,
                "comparison": dict(getattr(fact, "comparison", {}) or {}),
                "unit": getattr(fact, "unit", ""),
                "score": round(float(getattr(fact, "score", 0.0)), 4),
                "allowed_title_ids": list(getattr(fact, "allowed_title_ids", []) or []),
                "source_refs": list(getattr(fact, "source_refs", []) or []),
            }
        )
    return {
        "goal": goal_info,
        "must_not_assume_direction": True,
        "facts": catalog_facts,
        "allowed_roles": ["answer", "context", "evidence", "contrast", "limitation"],
        "allowed_transitions": [
            "none",
            "context",
            "elaboration",
            "contrast",
            "drill_down",
            "temporal",
        ],
        "allowed_layout_intents": [
            "editorial",
            "comparison",
            "dashboard",
            "kpi_cards",
            "big_number",
            "chart_takeaway",
            "table_topn",
            "callout",
        ],
    }


def classify_fact_transition(f_prev: object, f_next: object) -> tuple[str, float]:
    """Классифицирует допустимый переход между двумя фактами в графе переходов
    (context / elaboration / contrast / drill_down / temporal) и возвращает
    (transition_name, L_score). Причинных рёбер в графе нет."""
    from deckdna.analysis.fact_ledger import is_contrast

    if is_contrast(f_prev, f_next):
        return "contrast", 0.92

    prev_kind = getattr(f_prev, "kind", "") or ""
    next_kind = getattr(f_next, "kind", "") or ""
    prev_pred = getattr(f_prev, "predicate", "") or ""
    next_pred = getattr(f_next, "predicate", "") or ""
    prev_metric = (getattr(f_prev, "metric", "") or "").strip().casefold()
    next_metric = (getattr(f_next, "metric", "") or "").strip().casefold()
    prev_ds = getattr(f_prev, "dataset_id", "") or ""
    next_ds = getattr(f_next, "dataset_id", "") or ""
    prev_scope = getattr(f_prev, "scope", {}) or {}
    next_scope = getattr(f_next, "scope", {}) or {}

    if next_kind in ("missing", "missing_data", "incomplete_period", "outlier"):
        return "contrast", 0.85

    opposite_pairs = {
        ("increase", "decrease"),
        ("decrease", "increase"),
        ("steady_increase", "steady_decrease"),
        ("steady_decrease", "steady_increase"),
        ("every_month_increase", "every_month_decrease"),
        ("every_month_decrease", "every_month_increase"),
        ("leader", "laggard"),
        ("laggard", "leader"),
        ("positive_association", "negative_association"),
        ("negative_association", "positive_association"),
    }
    if (prev_pred, next_pred) in opposite_pairs:
        return "contrast", 0.90

    # drill_down: от общего итога / тренда / KPI к разбивке по категориям, долям или лидерам
    prev_is_global = (not prev_scope or prev_scope.get("slice") in ("all", None)) and prev_kind in (
        "kpi",
        "kpi_value",
        "endpoint_change",
        "steady_trend",
        "strict_trend",
        "monotonic_trend",
        "bridge",
    )
    next_is_breakdown = next_kind in (
        "rank_leader",
        "rank_laggard",
        "share",
        "comparison",
        "concentration",
        "top_n_share",
        "driver",
        "additive_driver",
        "dispersion",
        "distribution",
    )
    if prev_is_global and next_is_breakdown and (prev_ds == next_ds or prev_metric == next_metric):
        return "drill_down", 0.95
    if (
        prev_ds == next_ds
        and prev_kind in ("rank_leader", "share")
        and next_kind
        in (
            "comparison",
            "concentration",
            "top_n_share",
            "rank_laggard",
            "driver",
            "additive_driver",
        )
    ):
        return "drill_down", 0.88

    # Обратный ход от частного сегмента к глобальному итогу той же метрики уступает answer-first
    prev_is_breakdown = bool(prev_scope and prev_scope.get("slice") not in ("all", None))
    next_is_global = not next_scope or next_scope.get("slice") in ("all", None)
    if prev_is_breakdown and next_is_global and (prev_ds == next_ds or prev_metric == next_metric):
        return "context", 0.45

    # temporal: переход между временными срезами или трендами
    temporal_kinds = {
        "endpoint_change",
        "steady_trend",
        "strict_trend",
        "monotonic_trend",
        "peak",
        "acceleration",
        "deceleration",
    }
    prev_period = (getattr(f_prev, "period", "") or "").strip()
    next_period = (getattr(f_next, "period", "") or "").strip()
    if (prev_kind in temporal_kinds and next_kind in temporal_kinds) or (
        prev_period and next_period and prev_period != next_period and prev_metric == next_metric
    ):
        return "temporal", 0.85

    # elaboration: развитие темы по тому же датасету или метрике
    if prev_ds == next_ds or (prev_metric and prev_metric == next_metric):
        return "elaboration", 0.80

    # context: переход к смежному набору данных / контекстуальной метрике
    return "context", 0.65


def story_sequence_objective(
    facts: _Sequence[object],
    *,
    coherence_weight: float = 0.20,
    redundancy_weight: float = 0.30,
    length_penalty: float = 0.08,
) -> float:
    """Целевая функция последовательности фактов π (§5 выжимки):
    J(π) = Σ S(f_i) + 0.20·Σ L(f_i, f_{i+1}) − 0.30·Σ D(f_i, prefix) − 0.08·|π|."""
    from deckdna.analysis.fact_ledger import fact_similarity

    seq = list(facts)
    if not seq:
        return 0.0
    sum_s = sum(max(0.0, float(getattr(f, "score", 0.0))) for f in seq)
    sum_l = 0.0
    sum_d = 0.0
    for idx in range(1, len(seq)):
        _, l_val = classify_fact_transition(seq[idx - 1], seq[idx])
        sum_l += l_val
        max_sim = max(fact_similarity(seq[idx], prev) for prev in seq[:idx])
        sum_d += max_sim
    total = sum_s + coherence_weight * sum_l - redundancy_weight * sum_d - length_penalty * len(seq)
    return round(total, 6)


def _select_title_realization_for_fact(fact: object, goal_info: dict[str, Any]) -> str:
    """Выбирает допустимый шаблон заголовка из fact.allowed_title_ids.
    Если цель предполагала рост (assumed_direction == 'increase'), а факт показывает
    снижение (predicate == 'decrease'), выбирает честный шаблон 'no_growth_contrast'."""
    allowed = list(getattr(fact, "allowed_title_ids", []) or [])
    if not allowed:
        return ""
    assumed = goal_info.get("assumed_direction")
    pred = getattr(fact, "predicate", "") or ""
    if (
        assumed == "increase"
        and pred in ("decrease", "steady_decrease", "every_month_decrease")
        and "no_growth_contrast" in allowed
    ):
        return "no_growth_contrast"
    return allowed[0]


def _default_layout_intent_for_fact(fact: object, role: str) -> str:
    kind = getattr(fact, "kind", "") or ""
    if role == "limitation" or kind in ("missing", "missing_data", "incomplete_period"):
        return "callout"
    if kind in ("kpi", "kpi_value"):
        return "kpi_cards"
    if kind in ("reference", "reference_summary"):
        return "table_topn"
    if role == "contrast":
        return "comparison"
    if role == "answer":
        return "editorial"
    return "editorial"


def beam_search_story_plan(
    ledger: object,
    *,
    brief: object | None = None,
    brief_text: str | None = None,
    max_slides: int | None = None,
    target_slide_count: int | None = None,
    k: int = 8,
    beam_width: int | None = None,
) -> StoryLedgerPlan:
    """Детерминированный поиск оптимального плана истории методом beam search (k=8)
    по графу переходов (context / elaboration / contrast / drill_down / temporal)
    с максимизацией J = ΣS + 0.20·ΣL − 0.30·ΣD − 0.08·|π|."""
    from deckdna.analysis.fact_ledger import are_contradictory, fact_similarity

    purpose = (
        brief_text
        if brief_text is not None
        else (getattr(brief, "purpose", None) if brief is not None else None)
    )
    goal_info = neutralize_goal_direction(purpose)

    by_id: dict[str, Any] = getattr(ledger, "by_id", {}) or {}
    ordered_ids = _ordered_fact_ids(ledger)
    all_facts = [by_id[fid] for fid in ordered_ids if fid in by_id]

    substantive = [
        f for f in all_facts if _is_substantive_fact(f) and float(getattr(f, "score", 0.0)) > 0.0
    ]
    caveats = [f for f in all_facts if not _is_substantive_fact(f)]

    # 1. Если содержательных фактов нет (0) — честный слайд об ограничениях данных
    if not substantive:
        caveat_ids = [f.id for f in caveats[:2]]
        primary_caveat = caveats[0] if caveats else None
        title_id = (
            _select_title_realization_for_fact(primary_caveat, goal_info)
            if primary_caveat is not None
            else ""
        )
        omitted = [f.id for f in all_facts if f.id not in set(caveat_ids)]
        return StoryLedgerPlan(
            main_fact_id=caveat_ids[0] if caveat_ids else "",
            slides=[
                StoryLedgerSlide(
                    role="limitation",
                    fact_ids=caveat_ids,
                    title_realization_id=title_id,
                    transition="none",
                    layout_intent="callout",
                )
            ],
            omitted_fact_ids=omitted,
        )

    # При нейтральной цели (must_not_assume_direction) сортировка опирается только на силу факта,
    # не отбрасывая факты противоположного направления
    candidates = sorted(
        substantive,
        key=lambda f: (-float(getattr(f, "score", 0.0)), f.id),
    )

    target_total = (
        max_slides or target_slide_count or getattr(brief, "target_slide_count", None) or 8
    )
    # Длина по материалу: не больше числа содержательных фактов и не больше бюджета контента
    max_content_steps = max(1, min(len(candidates), int(target_total) - 2))

    eff_k = beam_width if beam_width is not None else k
    beam_w = max(1, int(eff_k))
    # Answer-first (принцип пирамиды §5): первый слайд (role='answer', main_fact_id)
    # стартует с лидирующих по силе фактов (в пределах 0.03 от максимума S(f))
    top_s = float(getattr(candidates[0], "score", 0.0))
    answer_roots = [
        f for f in candidates[:beam_w] if float(getattr(f, "score", 0.0)) >= top_s - 0.03
    ] or [candidates[0]]
    beam: list[tuple[float, list[Any]]] = [
        (story_sequence_objective([f]), [f]) for f in answer_roots
    ]
    beam.sort(key=lambda item: (-item[0], [x.id for x in item[1]]))
    best_overall: tuple[float, list[Any]] = beam[0]

    for _step in range(2, max_content_steps + 1):
        next_beam: list[tuple[float, list[Any]]] = []
        seen_signatures: set[tuple[str, ...]] = set()
        for prev_score, seq in beam:
            used_ids = {f.id for f in seq}
            last_fact = seq[-1]
            for cand in candidates:
                if cand.id in used_ids:
                    continue
                if any(are_contradictory(cand, prev_f) for prev_f in seq):
                    continue
                _, l_val = classify_fact_transition(last_fact, cand)
                max_sim = max(fact_similarity(cand, prev_f) for prev_f in seq)
                # Порог избыточности: почти идентичный факт (sim >= 0.80) не занимает отдельный
                # слайд
                if max_sim >= 0.80:
                    continue
                marginal_j = (
                    float(getattr(cand, "score", 0.0)) + 0.20 * l_val - 0.30 * max_sim - 0.08
                )
                # Штраф длины отсекает слабые/избыточные факты (нет добивки)
                if marginal_j <= 0.0:
                    continue
                new_seq = [*seq, cand]
                sig = tuple(f.id for f in new_seq)
                if sig in seen_signatures:
                    continue
                seen_signatures.add(sig)
                new_score = round(prev_score + marginal_j, 6)
                next_beam.append((new_score, new_seq))

        if not next_beam:
            break
        next_beam.sort(key=lambda item: (-item[0], [x.id for x in item[1]]))
        beam = next_beam[:beam_w]
        if beam[0][0] > best_overall[0]:
            best_overall = beam[0]

    chosen_seq = best_overall[1]
    used_fact_ids: set[str] = {f.id for f in chosen_seq}

    slides: list[StoryLedgerSlide] = []
    grouped_ids: set[str] = set()
    for idx, fact in enumerate(chosen_seq):
        if fact.id in grouped_ids:
            continue
        if idx == 0:
            role = "answer"
            transition = "none"
        else:
            transition, _ = classify_fact_transition(chosen_seq[idx - 1], fact)
            if transition == "contrast":
                role = "contrast"
            elif transition == "context" and idx == 1:
                role = "context"
            else:
                role = "evidence"

        slide_fact_ids = [fact.id]
        # Несколько выбранных KPI одного представления — одна полоса карточек.
        # Группируем только факты из результата поиска, не возвращая отсеянные.
        if fact.kind in {"kpi", "kpi_value"}:
            companions = [
                f.id
                for f in chosen_seq
                if f.id != fact.id
                and f.kind in {"kpi", "kpi_value"}
                and f.dataset_id == fact.dataset_id
                and f.view_id == fact.view_id
            ]
            slide_fact_ids.extend(companions)
            grouped_ids.update(companions)

        # Если к этому датасету относится оговорка о пропусках — прикрепляем к слайду
        for cav in caveats:
            if cav.id not in used_fact_ids and cav.dataset_id == fact.dataset_id:
                slide_fact_ids.append(cav.id)
                used_fact_ids.add(cav.id)
                break

        title_id = _select_title_realization_for_fact(fact, goal_info)
        layout_intent = _default_layout_intent_for_fact(fact, role)
        slides.append(
            StoryLedgerSlide(
                role=role,
                fact_ids=slide_fact_ids,
                title_realization_id=title_id,
                transition=transition,
                layout_intent=layout_intent,
            )
        )

    omitted = [f.id for f in all_facts if f.id not in used_fact_ids]
    return StoryLedgerPlan(
        main_fact_id=chosen_seq[0].id,
        slides=slides,
        omitted_fact_ids=omitted,
    )


def validate_story_ledger_plan(
    raw_plan: StoryLedgerPlan | dict[str, Any] | str | None,
    ledger: object,
) -> StoryLedgerValidation:
    """Серверная валидация JSON-ответа Story Director:
    1) все fact_id существуют в ledger.by_id;
    2) title_realization_id ∈ fact.allowed_title_ids и рендерится через render_title;
    3) у каждого evidence/answer/contrast-слайда есть хотя бы один факт;
    4) переходы принадлежат графу переходов (причинные переходы запрещены);
    5) выбранные факты не противоречат друг другу."""
    import json as _json

    from deckdna.analysis.fact_ledger import are_contradictory
    from deckdna.analysis.title_templates import render_title

    if raw_plan is None:
        return StoryLedgerValidation(ok=False, reasons=["empty_plan"], plan=None)

    parsed: StoryLedgerPlan
    if isinstance(raw_plan, StoryLedgerPlan):
        parsed = raw_plan
    elif isinstance(raw_plan, str):
        try:
            data = _json.loads(raw_plan)
            parsed = StoryLedgerPlan.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            return StoryLedgerValidation(
                ok=False, reasons=[f"invalid_json_or_schema:{exc}"], plan=None
            )
    elif isinstance(raw_plan, dict):
        try:
            parsed = StoryLedgerPlan.model_validate(raw_plan)
        except Exception as exc:  # noqa: BLE001
            return StoryLedgerValidation(ok=False, reasons=[f"invalid_schema:{exc}"], plan=None)
    else:
        return StoryLedgerValidation(ok=False, reasons=["unsupported_plan_type"], plan=None)

    by_id: dict[str, Any] = getattr(ledger, "by_id", {}) or {}
    substantive_in_ledger = [f for f in by_id.values() if _is_substantive_fact(f)]
    reasons: list[str] = []

    if not parsed.slides:
        reasons.append("empty_slides")
        return StoryLedgerValidation(ok=False, reasons=reasons, plan=None)

    if substantive_in_ledger:
        if not parsed.main_fact_id:
            reasons.append("missing_main_fact_id")
        elif parsed.main_fact_id not in by_id:
            reasons.append(f"unknown_main_fact_id:{parsed.main_fact_id}")
    elif parsed.main_fact_id and parsed.main_fact_id not in by_id:
        reasons.append(f"unknown_main_fact_id:{parsed.main_fact_id}")

    for oid in parsed.omitted_fact_ids:
        if oid not in by_id:
            reasons.append(f"unknown_omitted_fact_id:{oid}")

    selected_facts: list[Any] = []
    for idx, sl in enumerate(parsed.slides):
        role = (sl.role or "").strip().lower()
        transition = (sl.transition or "none").strip().lower()
        if role not in ALLOWED_STORY_ROLES:
            reasons.append(f"slide_{idx}_invalid_role:{sl.role}")
        if transition in FORBIDDEN_CAUSAL_TRANSITIONS or "caus" in transition:
            reasons.append(f"slide_{idx}_forbidden_causal_transition:{sl.transition}")
        elif transition not in ALLOWED_TRANSITIONS:
            reasons.append(f"slide_{idx}_invalid_transition:{sl.transition}")

        if sl.layout_intent not in build_fact_ledger_catalog(ledger)["allowed_layout_intents"]:
            reasons.append(f"slide_{idx}_invalid_layout_intent:{sl.layout_intent}")

        # Модель не пишет чисел в полях схемы
        if re.search(r"\d", sl.role or "") or re.search(r"\d", sl.transition or ""):
            reasons.append(f"slide_{idx}_numbers_in_structural_fields")

        if role in ("evidence", "answer", "contrast") and not sl.fact_ids:
            reasons.append(f"slide_{idx}_evidence_slide_missing_fact")
            continue

        if not sl.fact_ids:
            if substantive_in_ledger and role != "limitation":
                reasons.append(f"slide_{idx}_missing_facts")
            continue

        slide_facts: list[Any] = []
        for fid in sl.fact_ids:
            if fid not in by_id:
                reasons.append(f"slide_{idx}_unknown_fact_id:{fid}")
            else:
                slide_facts.append(by_id[fid])

        if len(slide_facts) != len(sl.fact_ids):
            continue

        primary = slide_facts[0]
        allowed_titles = list(getattr(primary, "allowed_title_ids", []) or [])
        if not sl.title_realization_id:
            reasons.append(f"slide_{idx}_missing_title_realization_id")
        elif sl.title_realization_id not in allowed_titles:
            reasons.append(f"slide_{idx}_disallowed_title_realization_id:{sl.title_realization_id}")
        else:
            try:
                rendered = render_title(sl.title_realization_id, primary)
                if not rendered or not rendered.strip():
                    reasons.append(f"slide_{idx}_empty_rendered_title")
            except Exception as exc:  # noqa: BLE001
                reasons.append(f"slide_{idx}_render_title_failed:{exc}")

        for sf in slide_facts:
            for prev_f in selected_facts:
                if are_contradictory(sf, prev_f):
                    reasons.append(f"slide_{idx}_contradictory_facts:{prev_f.id}_vs_{sf.id}")
            selected_facts.append(sf)

    selected_ids = {f.id for f in selected_facts}
    if parsed.main_fact_id and parsed.main_fact_id not in selected_ids:
        reasons.append("main_fact_not_selected")
    if selected_ids.intersection(parsed.omitted_fact_ids):
        reasons.append("selected_fact_marked_omitted")
    if len(selected_facts) != len(selected_ids):
        reasons.append("repeated_fact_id")

    if substantive_in_ledger and not any(_is_substantive_fact(f) for f in selected_facts):
        reasons.append("no_substantive_facts_selected")

    if reasons:
        return StoryLedgerValidation(ok=False, reasons=reasons, plan=None)
    return StoryLedgerValidation(ok=True, reasons=[], plan=parsed)


def resolve_story_ledger_plan(
    raw_plan: StoryLedgerPlan | dict[str, Any] | str | None,
    ledger: object,
    *,
    brief: object | None = None,
    brief_text: str | None = None,
    max_slides: int | None = None,
    target_slide_count: int | None = None,
    k: int = 8,
    beam_width: int | None = None,
) -> StoryLedgerPlan:
    """Проверяет ответ модели через validate_story_ledger_plan.
    Если ответ невалиден — возвращает детерминированный план
    beam_search_story_plan (без падения).
    """
    validation = validate_story_ledger_plan(raw_plan, ledger)
    budget = max_slides or target_slide_count or getattr(brief, "target_slide_count", None)
    if (
        validation.ok
        and validation.plan is not None
        and (budget is None or len(validation.plan.slides) <= max(1, int(budget) - 2))
    ):
        return validation.plan
    return beam_search_story_plan(
        ledger,
        brief=brief,
        brief_text=brief_text,
        max_slides=max_slides,
        target_slide_count=target_slide_count,
        k=k,
        beam_width=beam_width,
    )


def _fact_bullet_text(fact: object, *, variant_name: str = "comparison") -> str:
    """Формирует проверяемый текстовый пункт по факту через render_title
    без генерации чисел моделью."""
    from deckdna.analysis.title_templates import render_title

    allowed = list(getattr(fact, "allowed_title_ids", []) or [])
    tid = allowed[0] if allowed else ""
    core = render_title(tid, fact) if tid else ""
    if not core:
        metric = getattr(fact, "metric", "Показатель") or "Показатель"
        core = f"{metric}: подтверждённый факт из реестра"
    if _CAUSAL_CLAIM_TEXT_RE.search(core):
        core = _CAUSAL_CLAIM_TEXT_RE.sub("при этом", core)
    return core


def _non_numeric_commentary_for_fact(
    fact: object, *, variant_name: str = "comparison"
) -> str | None:
    """Нечисловой аналитический акцент («so what»), зависящий от варианта
    (editorial / comparison / dashboard): меняет плотность слов, но НЕ содержит цифр."""
    kind = getattr(fact, "kind", "") or ""
    pred = getattr(fact, "predicate", "") or ""
    if variant_name == "dashboard":
        return None
    if variant_name == "editorial":
        mapping = {
            "increase": (
                "Динамика положительная: стоит закрепить факторы текущего "
                "изменения в операционном плане"
            ),
            "decrease": (
                "Динамика отрицательная: требуется проверить сегменты и "
                "скорректировать базовый план"
            ),
            "leader": (
                "Лидирующий сегмент формирует основную базу: приоритет распределения ресурсов"
            ),
            "concentration": (
                "Высокая концентрация в верхних сегментах требует контроля устойчивости структуры"
            ),
            "positive_association": (
                "Наблюдается статистическая связь показателей; "
                "причинно-следственную зависимость проверять отдельно"
            ),
            "negative_association": (
                "Наблюдается обратная статистическая связь; причинно-следственную "
                "зависимость проверять отдельно"
            ),
            "range": (
                "Разброс значений учитывается при планировании операционного коридора и резервов"
            ),
            "typical": (
                "Типичный уровень показателя служит ориентиром для базового сценария планирования"
            ),
        }
        return mapping.get(
            pred,
            "Подтверждено расчётом по исходному набору данных без ручных допущений"
            if kind != "kpi_value"
            else None,
        )
    mapping_mid = {
        "increase": "Динамика положительная: закрепить факторы изменения в плане",
        "decrease": "Динамика отрицательная: проверить сегменты и обновить план",
        "leader": "Лидирующий сегмент задаёт приоритет распределения ресурсов",
        "concentration": "Высокая концентрация требует контроля устойчивости структуры",
        "positive_association": "Связь показателей статистическая; причинность проверять отдельно",
        "negative_association": "Обратная статистическая связь; причинность проверять отдельно",
        "range": "Учитывать диапазон значений при планировании резервов",
        "typical": "Ориентир для базового сценария планирования",
    }
    return mapping_mid.get(pred)


def ledger_source_groups(pack: object | None, brief: object) -> list:
    """Сохранить авторский контекст отдельно от вычисленных аналитических фактов."""
    if pack is None:
        return []
    from deckdna.errors import DeckDNAError
    from deckdna.planning.data_story import _data_only
    from deckdna.planning.mandatory import mandatory_anchors, protect_mandatory_groups
    from deckdna.planning.story_director import content_groups

    required = {s.id for s, _ in mandatory_anchors(pack, brief.mandatory_sections)}
    files = {d.source_ref.artifact_id for d in pack.datasets}
    groups = [
        (section, [u for u in units if u.kind.value not in {"table", "chart"}])
        for section, units in protect_mandatory_groups(content_groups(pack), pack, brief)
        if section.id in required or not _data_only(section, files)
    ]
    groups = [(s, units) for s, units in groups if units]
    room = max(0, brief.target_slide_count - 3)
    if len(required) > room:
        raise DeckDNAError(
            "validation_failed",
            "required source sections exceed the slide budget",
            stage="planning",
            details={"rule": "request.mandatory_section"},
        )
    selected = {s.id for s, _ in groups if s.id in required}
    for section, _ in groups:
        if len(selected) >= room:
            break
        selected.add(section.id)
    return [(section, units) for section, units in groups if section.id in selected]


def compile_story_ledger_to_deck_plan(
    story_plan: StoryLedgerPlan,
    ledger: object,
    brief: object,
    *,
    pack: object | None = None,
    strategy: object = "balanced",
    config: object | None = None,
) -> object:
    """Компилирует валидированный StoryLedgerPlan в DeckPlan.
    Гарантирует:
    - Все заголовки слайдов с фактами рендерятся строго через render_title(title_realization_id,
    fact);
    - При 0 содержательных фактов создаётся честный слайд «Что можно установить и каких данных
    не хватает»;
    - При 2 содержательных фактах колода состоит из 3–4 слайдов без разделителей-заглушек;
    - Между тремя вариантами (editorial/faithful, comparison/balanced, dashboard/visual)
      одинаковы fact_ids, числа, типы графиков и порядок категорий;
      меняются композиция, акценты и плотность.
    """
    import hashlib as _hashlib

    from deckdna.analysis.title_templates import render_title
    from deckdna.contracts.deck_plan import (
        Brief,
        ChartSpec,
        ChartType,
        ContentUnit,
        DeckPlan,
        DensityBudget,
        DesiredVisual,
        Kind,
        Level,
        Provenance,
        Purpose,
        SlidePlan,
    )
    from deckdna.contracts.deck_plan import (
        Section as PlanSection,
    )

    strat_key = getattr(strategy, "value", str(strategy or "balanced")).lower()
    profile = VARIANT_LAYOUT_PROFILES.get(strat_key, VARIANT_LAYOUT_PROFILES["balanced"])
    variant_name = profile["variant_name"]
    density_level = Level(profile["density_level"])

    by_id: dict[str, Any] = getattr(ledger, "by_id", {}) or {}
    pack_id = getattr(pack, "id", None) or "pack-ledger"
    lang = getattr(brief, "language", None) or getattr(pack, "language", None) or "ru"
    ru = lang.lower().startswith("ru")
    meta_ev = [f"{pack_id}:meta"]

    from deckdna.planning.story_director import (
        clean_purpose_text,
        cover_context,
        cover_topic,
        split_cover_headline,
    )

    goal_info = neutralize_goal_direction(getattr(brief, "purpose", None))
    raw_cover = (
        cover_topic(getattr(pack, "title_hint", None))
        or clean_purpose_text(getattr(brief, "purpose", None))
        or (goal_info["neutral_question"] or "").rstrip("? ")
        or ("Аналитический обзор" if ru else "Analytical Review")
    )
    split_head, split_sub = split_cover_headline(raw_cover)
    cover_title = split_head or raw_cover
    neutral_brief = (
        brief.model_copy(update={"key_message": None})
        if hasattr(brief, "model_copy")
        else brief
    )
    cover_eyebrow, cover_subtitle = cover_context(
        cover_title, neutral_brief, fallback_subtitle=split_sub
    )
    cover_subtitle = (
        cover_subtitle
        or split_sub
        or clean_purpose_text(getattr(brief, "purpose", None))
    )

    # Индексируем графики и таблицы пака по view_id / dataset_id
    charts_by_view: dict[str, Any] = {}
    tables_by_view: dict[str, Any] = {}
    if pack is not None:
        for ch in getattr(pack, "charts", None) or []:
            rng = getattr(getattr(ch, "source_ref", None), "range", None)
            if rng:
                charts_by_view[rng] = ch
            charts_by_view.setdefault(ch.id, ch)
        for tb in getattr(pack, "tables", None) or []:
            rng = getattr(getattr(tb, "source_ref", None), "range", None)
            if rng:
                tables_by_view[rng] = tb
            tables_by_view.setdefault(tb.id, tb)

    view_analyses: dict[str, Any] = {}
    if pack is not None and pack.datasets:
        from deckdna.analysis.pipeline import analyze_pack

        view_analyses = {
            va.view.id: va
            for analysis in analyze_pack(pack, brief.purpose)
            for va in analysis.views
        }

    slides: list[SlidePlan] = []

    def _emit(sp: SlidePlan) -> None:
        slides.append(sp.model_copy(update={"index": len(slides), "id": f"slide-{len(slides)}"}))

    # 1. Обложка
    cover_units = [
        ContentUnit(role="title", kind=Kind.title, text=cover_title, evidence_ids=meta_ev)
    ]
    if cover_eyebrow:
        cover_units.append(
            ContentUnit(
                role="eyebrow",
                kind=Kind.subtitle,
                text=cover_eyebrow,
                evidence_ids=meta_ev,
            )
        )
    if cover_subtitle and cover_subtitle.strip().casefold() != cover_title.strip().casefold():
        cover_units.append(
            ContentUnit(
                role="subtitle",
                kind=Kind.subtitle,
                text=cover_subtitle,
                evidence_ids=meta_ev,
            )
        )
    _emit(
        SlidePlan(
            id="slide-0",
            index=0,
            purpose=Purpose.title,
            title_intent=cover_title,
            key_message=goal_info["neutral_question"],
            evidence_ids=meta_ev,
            content_units=cover_units,
            density_budget=DensityBudget(level=Level.sparse, max_items=1),
        )
    )

    # Проверяем наличие содержательных фактов в плане
    substantive_selected = [
        by_id[fid]
        for sl in story_plan.slides
        for fid in sl.fact_ids
        if fid in by_id and _is_substantive_fact(by_id[fid])
    ]

    if not substantive_selected:
        # Честный слайд «Что можно установить и каких данных не хватает»
        lim_slide = story_plan.slides[0] if story_plan.slides else None
        lim_facts = [
            by_id[fid] for fid in (lim_slide.fact_ids if lim_slide else []) if fid in by_id
        ]
        if lim_facts and lim_slide and lim_slide.title_realization_id:
            try:
                lim_title = render_title(lim_slide.title_realization_id, lim_facts[0])
            except Exception:  # noqa: BLE001
                lim_title = "Что можно установить и каких данных не хватает"
        else:
            lim_title = "Что можно установить и каких данных не хватает"

        bullet_texts: list[tuple[str, list[str]]] = []
        for lf in lim_facts:
            b_txt = _fact_bullet_text(lf, variant_name=variant_name)
            bullet_texts.append((b_txt, [f"{pack_id}:{lf.id}"]))
        if not bullet_texts:
            bullet_texts.append(
                (
                    "Что можно установить: в переданных таблицах нет валидных "
                    "числовых рядов для расчёта выводов",
                    meta_ev,
                )
            )
        bullet_texts.append(
            (
                "Каких данных не хватает: нужны заполненные числовые показатели, "
                "единицы измерения и сопоставимые периоды",
                meta_ev,
            )
        )
        lim_ev = list(dict.fromkeys(eid for _, evs in bullet_texts for eid in evs)) or meta_ev
        unit_role = "callout" if variant_name != "comparison" else "comparison"
        _emit(
            SlidePlan(
                id="slide-1",
                index=1,
                purpose=Purpose.problem,
                title_intent=lim_title,
                key_message=bullet_texts[0][0],
                evidence_ids=lim_ev,
                content_units=[
                    ContentUnit(
                        role="title",
                        kind=Kind.title,
                        text=lim_title,
                        evidence_ids=lim_ev[:1],
                    ),
                    *(
                        ContentUnit(
                            role=unit_role,
                            kind=Kind.bullet,
                            text=txt,
                            evidence_ids=evs,
                        )
                        for txt, evs in bullet_texts
                    ),
                ],
                density_budget=DensityBudget(level=density_level, max_items=len(bullet_texts)),
                speaker_note="Данных недостаточно для статистически корректных числовых выводов.",
                insight_ids=[lf.id for lf in lim_facts],
            )
        )
    else:
        role_to_purpose = {
            "answer": Purpose.data,
            "evidence": Purpose.data,
            "contrast": Purpose.comparison,
            "context": Purpose.overview,
            "limitation": Purpose.problem,
            "recommendation": Purpose.cta,
        }
        variant_bullet_role = {
            "editorial": "kpi",
            "comparison": "comparison",
            "dashboard": "big-number",
        }.get(variant_name, "kpi")

        primary_views = [
            (by_id[s.fact_ids[0]].view_id or by_id[s.fact_ids[0]].dataset_id)
            for s in story_plan.slides
            if s.fact_ids and s.fact_ids[0] in by_id
        ]
        for story_index, sl in enumerate(story_plan.slides):
            slide_facts = [by_id[fid] for fid in sl.fact_ids if fid in by_id]
            if not slide_facts:
                continue
            primary = slide_facts[0]
            tid = sl.title_realization_id or _select_title_realization_for_fact(primary, goal_info)
            title_text = render_title(tid, primary)
            ev_ids = [f"{pack_id}:{f.id}" for f in slide_facts]
            units: list[ContentUnit] = [
                ContentUnit(
                    role="title",
                    kind=Kind.title,
                    text=title_text,
                    evidence_ids=ev_ids[:1],
                )
            ]

            # График или таблица при наличии представления в паке (тип графика и порядок
            # категорий инвариантны!)
            view_key = getattr(primary, "view_id", None) or primary.dataset_id
            chart_obj = charts_by_view.get(view_key) or charts_by_view.get(primary.dataset_id)
            table_obj = tables_by_view.get(view_key) or tables_by_view.get(primary.dataset_id)
            desired_vis = DesiredVisual.none
            chart_card_dict: dict[str, Any] | None = None
            if chart_obj is not None or table_obj is not None:
                chart_card_dict = {
                    "type": chart_obj.chart_type if chart_obj is not None else "table",
                    "reasons": ["выбрано детерминированными правилами по типу среза"],
                    "alternatives": [],
                    "view_id": view_key,
                    "layout_intent": profile["chart_layout_intent"],
                }

            # Главный тезис не дублирует график, который далее служит доказательством.
            defer_answer_chart = sl.role == "answer" and any(
                later.role != "answer"
                and later.fact_ids
                and (by_id[later.fact_ids[0]].view_id or by_id[later.fact_ids[0]].dataset_id)
                == view_key
                for later in story_plan.slides[story_index + 1 :]
            )
            va = view_analyses.get(view_key)
            # Доля от того же среза — отдельный факт, но не второй одинаковый график.
            derived_duplicate = bool(
                va
                and va.view.derived
                and any(
                    other.view.id in primary_views
                    and not other.view.derived
                    and other.view.dataset_id == va.view.dataset_id
                    and other.view.categories == va.view.categories
                    and other.rec.chosen.chart_type == va.rec.chosen.chart_type
                    for other in view_analyses.values()
                )
            )
            summary_only = defer_answer_chart or derived_duplicate
            if (
                chart_obj is not None
                and chart_obj.chart_type not in {"kpi", "table"}
                and not summary_only
            ):
                raw_ct = getattr(chart_obj, "chart_type", None) or "bar"
                try:
                    ct_enum = ChartType(raw_ct)
                except ValueError:
                    ct_enum = ChartType.bar
                units.append(
                    ContentUnit(
                        role="chart",
                        kind=Kind.chart,
                        chart_ref=chart_obj.id,
                        evidence_ids=[f"{pack_id}:{primary.dataset_id}"],
                        chart_spec=ChartSpec(
                            chart_type=ct_enum,
                            series_count=min(len(getattr(chart_obj, "series", []) or [1]), 5),
                            has_axis_labels=True,
                            has_units=bool(primary.unit),
                            has_legend=len(getattr(chart_obj, "series", []) or []) >= 2,
                        ),
                    )
                )
                desired_vis = DesiredVisual.chart
                chart_card_dict = {
                    "type": ct_enum.value,
                    "reasons": ["выбрано детерминированными правилами по типу среза"],
                    "alternatives": [],
                    "view_id": view_key,
                    "layout_intent": profile["chart_layout_intent"],
                }
            elif table_obj is not None and (
                primary.kind in {"reference", "reference_summary"}
                or (chart_obj is not None and chart_obj.chart_type == "table")
            ):
                units.append(
                    ContentUnit(
                        role="table",
                        kind=Kind.table,
                        table_ref=table_obj.id,
                        evidence_ids=[f"{pack_id}:{primary.dataset_id}"],
                    )
                )
                desired_vis = DesiredVisual.table

            if chart_card_dict is not None and view_key in view_analyses:
                from deckdna.planning.data_story import chart_card

                chart_card_dict = {
                    **chart_card(view_analyses[view_key], chart_card_dict["type"]),
                    "layout_intent": profile["chart_layout_intent"],
                }

            # Тезисы по всем фактам слайда (одинаковые факты и числа во всех 3 вариантах)
            for sf in slide_facts:
                b_text = _fact_bullet_text(sf, variant_name=variant_name)
                units.append(
                    ContentUnit(
                        role=variant_bullet_role,
                        kind=Kind.bullet,
                        text=b_text,
                        evidence_ids=[f"{pack_id}:{sf.id}"],
                    )
                )

            if summary_only and primary.source_refs:
                units.append(
                    ContentUnit(
                        role="source",
                        kind=Kind.bullet,
                        text="Источник: " + "; ".join(primary.source_refs),
                        evidence_ids=ev_ids,
                    )
                )

            # Нечисловой акцент (меняет плотность текста между editorial/comparison/dashboard
            # без изменения чисел)
            commentary = _non_numeric_commentary_for_fact(primary, variant_name=variant_name)
            if commentary:
                units.append(
                    ContentUnit(
                        role="callout" if variant_name == "editorial" else "comparison",
                        kind=Kind.bullet,
                        text=commentary,
                        evidence_ids=[f"{pack_id}:{primary.id}"],
                    )
                )

            slide_purpose = role_to_purpose.get(sl.role, Purpose.data)
            if desired_vis in (DesiredVisual.chart, DesiredVisual.table):
                slide_purpose = Purpose.data

            note_refs = (
                ", ".join(primary.source_refs) if primary.source_refs else primary.dataset_id
            )
            _emit(
                SlidePlan(
                    id="s",
                    index=0,
                    purpose=slide_purpose,
                    title_intent=title_text,
                    key_message=_fact_bullet_text(primary, variant_name=variant_name),
                    evidence_ids=ev_ids,
                    content_units=units,
                    desired_visual=desired_vis,
                    density_budget=DensityBudget(
                        level=density_level,
                        max_items=max(len(units) - 1, 1),
                    ),
                    speaker_note=f"Переход: {sl.transition}. Источник: {note_refs}.",
                    chart=chart_card_dict,
                    insight_ids=[f.id for f in slide_facts],
                )
            )

    # Дословный авторский контекст не становится вычисленным фактом реестра.
    for section, source_units in ledger_source_groups(pack, brief):
        ev_ids = list(dict.fromkeys(e for u in source_units for e in u.evidence_ids))
        _emit(
            SlidePlan(
                id="source",
                index=0,
                purpose=Purpose.custom,
                title_intent=section.heading,
                key_message=section.heading,
                evidence_ids=ev_ids,
                content_units=[
                    ContentUnit(
                        role="title", kind=Kind.title, text=section.heading, evidence_ids=ev_ids[:1]
                    ),
                    *source_units,
                ],
                density_budget=DensityBudget(level=density_level, max_items=len(source_units)),
                mandatory=any(u.locked for u in source_units),
            )
        )

    # Финальный слайд (без выдуманных чисел; одинаковые fact_ids и числа в 3 вариантах)
    main_fact = by_id.get(story_plan.main_fact_id) if story_plan.main_fact_id else None
    if main_fact is not None and _is_substantive_fact(main_fact):
        cta_ev = [f"{pack_id}:{main_fact.id}"]
        cta_title = "Рекомендации и следующие шаги" if ru else "Recommendations and next steps"
        cta_summary = _fact_bullet_text(main_fact, variant_name=variant_name)
        cta_comment = _non_numeric_commentary_for_fact(main_fact, variant_name=variant_name)
        cta_units = [
            ContentUnit(role="title", kind=Kind.title, text=cta_title, evidence_ids=cta_ev),
            ContentUnit(
                role="step" if variant_name != "dashboard" else "big-number",
                kind=Kind.bullet,
                text=cta_summary,
                evidence_ids=cta_ev,
            ),
        ]
        if cta_comment:
            cta_units.append(
                ContentUnit(
                    role="step",
                    kind=Kind.bullet,
                    text=cta_comment,
                    evidence_ids=cta_ev,
                )
            )
        _emit(
            SlidePlan(
                id="s",
                index=0,
                purpose=Purpose.cta,
                title_intent=cta_title,
                key_message=cta_summary,
                evidence_ids=cta_ev,
                content_units=cta_units,
                density_budget=DensityBudget(level=density_level, max_items=len(cta_units) - 1),
                insight_ids=[main_fact.id],
            )
        )
    else:
        thanks_title = "Следующие шаги: сбор данных" if ru else "Next steps: data collection"
        _emit(
            SlidePlan(
                id="s",
                index=0,
                purpose=Purpose.cta,
                title_intent=thanks_title,
                key_message=thanks_title,
                evidence_ids=meta_ev,
                content_units=[
                    ContentUnit(
                        role="title",
                        kind=Kind.title,
                        text=thanks_title,
                        evidence_ids=meta_ev,
                    ),
                    ContentUnit(
                        role="callout",
                        kind=Kind.bullet,
                        text=(
                            "Подготовить полные выгрузки с единицами измерения "
                            "и контрольными суммами"
                        ),
                        evidence_ids=meta_ev,
                    ),
                ],
                density_budget=DensityBudget(level=density_level, max_items=1),
            )
        )

    updated_brief = (
        brief.model_copy(update={"target_slide_count": max(len(slides), 3)})
        if isinstance(brief, Brief)
        else Brief(
            purpose=goal_info["neutral_question"],
            audience="руководство",
            language=lang,
            target_slide_count=max(len(slides), 3),
        )
    )
    ledger_hash = _hashlib.sha256(
        repr([(fid, by_id[fid]) for fid in sorted(by_id)]).encode("utf-8")
    ).hexdigest()
    return DeckPlan(
        schema_version="1.0",
        id=f"plan-{pack_id.removeprefix('pack-')}-{strat_key}",
        brief=updated_brief,
        evidence_graph_id=f"pack:{pack_id}",
        objective=goal_info["neutral_question"],
        audience=getattr(updated_brief, "audience", "руководство"),
        language=lang,
        sections=[
            PlanSection(
                id="story-ledger",
                title="Анализ по реестру фактов" if ru else "Fact ledger story",
                slide_ids=[s.id for s in slides if s.purpose != Purpose.title],
            )
        ],
        slides=slides,
        provenance=Provenance(
            planner="story-director/fact-ledger-1.0",
            prompt_version="v1.0",
            schema_version="1.0",
            model_id="deterministic",
            input_hashes=[ledger_hash],
        ),
    )


def plan_three_variants_from_ledger(
    ledger: object,
    brief: object,
    *,
    pack: object | None = None,
    story_plan: StoryLedgerPlan | None = None,
    config: object | None = None,
) -> dict[str, object]:
    """Строит три варианта колоды (editorial / comparison / dashboard, а также
    алиасы faithful / balanced / visual) из ОДНОГО семантического плана StoryLedgerPlan.
    Гарантирует одинаковые факты, числа, типы графиков и порядок категорий при разной
    композиции, акцентах и плотности."""
    budget = max(3, brief.target_slide_count - len(ledger_source_groups(pack, brief)))
    resolved_plan = resolve_story_ledger_plan(story_plan, ledger, brief=brief, max_slides=budget)
    editorial = compile_story_ledger_to_deck_plan(
        resolved_plan, ledger, brief, pack=pack, strategy="editorial", config=config
    )
    comparison = compile_story_ledger_to_deck_plan(
        resolved_plan, ledger, brief, pack=pack, strategy="comparison", config=config
    )
    dashboard = compile_story_ledger_to_deck_plan(
        resolved_plan, ledger, brief, pack=pack, strategy="dashboard", config=config
    )
    return {
        "editorial": editorial,
        "comparison": comparison,
        "dashboard": dashboard,
        "faithful": editorial,
        "balanced": comparison,
        "visual": dashboard,
    }


def layout_selection_cost(
    fit: float,
    family: str,
    history: _Sequence[str] = (),
) -> float:
    """Формула стоимости выбора семейства макета (§7 выжимки):
    cost = fit + 0.8·(family == prev) + 0.3·count_in_last_4."""
    prev = history[-1] if history else None
    count_in_last_4 = sum(1 for fam in history[-4:] if fam == family)
    return round(
        float(fit) + 0.8 * float(family == prev) + 0.3 * float(count_in_last_4),
        6,
    )


def select_layout_family(
    candidates: dict[str, float] | _Sequence[tuple[str, float] | dict[str, Any]],
    history: _Sequence[str] = (),
    *,
    chart_type: str | None = None,
) -> str:
    """Выбирает семейство макета для очередного слайда по формуле:
    cost = fit + 0.8·(family == prev) + 0.3·count_in_last_4.
    Три одинаковых семейства подряд запрещены, если есть подходящая альтернатива.
    Тип графика ради разнообразия не меняется."""
    _ = chart_type  # тип графика инвариантен и ради разнообразия не меняется
    norm: list[tuple[str, float, bool, int]] = []
    if isinstance(candidates, dict):
        for idx, (fam, fit_val) in enumerate(candidates.items()):
            norm.append((str(fam), float(fit_val), True, idx))
    else:
        for idx, item in enumerate(candidates):
            if isinstance(item, dict):
                fam = str(item["family"])
                fit_val = float(item.get("fit", 0.0))
                suitable = bool(item.get("suitable", True))
            else:
                fam, fit_val = str(item[0]), float(item[1])
                suitable = True
            norm.append((fam, fit_val, suitable, idx))

    if not norm:
        raise ValueError("candidates must not be empty")

    suitable_pool = [c for c in norm if c[2]] or norm
    forbid_streak_family: str | None = None
    if len(history) >= 2 and history[-1] == history[-2]:
        forbid_streak_family = history[-1]

    if forbid_streak_family is not None:
        alternatives = [c for c in suitable_pool if c[0] != forbid_streak_family]
        if alternatives:
            suitable_pool = alternatives

    best = min(
        suitable_pool,
        key=lambda c: (
            layout_selection_cost(c[1], c[0], history),
            c[1],
            c[3],
        ),
    )
    return best[0]


def select_layout_family_sequence(
    slides_candidates: _Sequence[dict[str, float] | _Sequence[tuple[str, float] | dict[str, Any]]],
    *,
    chart_types: _Sequence[str | None] | None = None,
) -> list[str]:
    """Последовательно выбирает семейства макетов для всей колоды,
    соблюдая формулу стоимости и запрет 3 одинаковых семейств подряд."""
    history: list[str] = []
    for idx, cands in enumerate(slides_candidates):
        ct = chart_types[idx] if chart_types is not None and idx < len(chart_types) else None
        chosen = select_layout_family(cands, history, chart_type=ct)
        history.append(chosen)
    return history
