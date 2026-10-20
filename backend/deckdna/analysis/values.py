"""Разбор сырых значений ячеек: числа, проценты, деньги, даты и периоды.

Один разбор для всех форматов (CSV, XLSX, JSON, таблица в тексте) — так
профиль и выбор графика не зависят от формата файла (ADR-033 §4)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

_MONTHS_RU = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}
_MONTHS_EN = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
MONTH_NAMES_RU = [
    "янв",
    "фев",
    "мар",
    "апр",
    "май",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
]

CURRENCIES = {"₽": "₽", "руб": "₽", "rub": "₽", "$": "$", "usd": "$", "€": "€", "eur": "€"}
SCALES = {
    "тыс": 1e3,
    "тыс.": 1e3,
    "k": 1e3,
    "млн": 1e6,
    "млн.": 1e6,
    "m": 1e6,
    "mln": 1e6,
    "млрд": 1e9,
    "млрд.": 1e9,
    "bn": 1e9,
    "b": 1e9,
}

_NUM_RE = re.compile(
    r"^(?P<pre>[^\d\-−+.,]*)(?P<num>[-−+]?\d(?:[\d\s\u00a0\u202f',.]*\d)?|[-−+]?[.,]\d+)"
    r"(?P<post>.*)$"
)


@dataclass(frozen=True)
class ParsedNumber:
    value: float
    percent: bool = False
    currency: str | None = None
    scale_word: str | None = None


def parse_number(raw: object) -> ParsedNumber | None:
    """Число из ячейки: «1 234,5», «12%», «₽ 3,5 млн», «-7.1», 42."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return ParsedNumber(float(raw))
    text = str(raw).strip()
    if not text or len(text) > 40:
        return None
    m = _NUM_RE.match(text)
    if not m:
        return None
    pre, num, post = m.group("pre").strip(), m.group("num"), m.group("post").strip()
    if pre and not any(c in pre.casefold() for c in ("₽", "$", "€", "руб", "usd", "eur")):
        return None
    num = re.sub(r"[\s\u00a0\u202f']", "", num).replace("−", "-")
    if "," in num and "." in num:
        # десятичный — последний из двух разделителей: 1,234.50 и 1.234,50
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif num.count(",") == 1:
        num = num.replace(",", ".")  # русская десятичная запятая
    elif num.count(",") > 1:
        num = num.replace(",", "")
    elif num.count(".") > 1:
        num = num.replace(".", "")
    try:
        value = float(num)
    except ValueError:
        return None
    tail = post.casefold().rstrip(".")
    percent = "%" in post
    currency = None
    for key, sign in CURRENCIES.items():
        if key in pre.casefold() or key in tail:
            currency = sign
            break
    scale_word = None
    words = re.findall(r"[a-zа-яё.]+", tail)
    for word in words:
        if word in SCALES or word.rstrip(".") in SCALES:
            scale_word = word.rstrip(".")
            break
    rest = re.sub(r"[%₽$€.\s]", "", tail)
    for word in (*CURRENCIES, *SCALES, "шт", "чел", "ед", "pcs"):
        rest = rest.replace(word, "")
    if rest and len(rest) > 2:
        return None  # «12 месяцев», «3 отдела» — текст, не число
    return ParsedNumber(value, percent=percent, currency=currency, scale_word=scale_word)


@dataclass(frozen=True)
class ParsedPeriod:
    """Дата или период. ``key`` сортируется по времени; ``grain`` —
    day | month | quarter | year."""

    key: tuple[int, int, int]
    grain: str
    label: str

    @property
    def as_date(self) -> date:
        y, m, d = self.key
        return date(y, max(m, 1), max(d, 1))


def _label(year: int, month: int = 0, day: int = 0, grain: str = "day") -> str:
    if grain == "year":
        return str(year)
    if grain == "quarter":
        return f"{(month - 1) // 3 + 1} кв. {year}"
    if grain == "month":
        return f"{MONTH_NAMES_RU[month - 1]} {year}"
    return f"{day:02d}.{month:02d}.{year}"


def period(year: int, month: int = 0, day: int = 0, grain: str = "day") -> ParsedPeriod:
    return ParsedPeriod((year, month, day), grain, _label(year, month, day, grain))


def parse_period(raw: object) -> ParsedPeriod | None:
    """«2025-03-14», «14.03.2025», «2025-03», «03.2025», «март 2025»,
    «Q1 2025», «1 кв. 2025», «2025»; datetime из XLSX."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return period(raw.year, raw.month, raw.day)
    if isinstance(raw, date):
        return period(raw.year, raw.month, raw.day)
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    if isinstance(raw, int):
        return period(raw, grain="year") if 1900 <= raw <= 2100 else None
    text = str(raw).strip().casefold()
    if not text or len(text) > 30:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t]\d{1,2}:\d{2}(?::\d{2})?)?", text)
    if m:
        y, mo, d = map(int, m.groups())
        return period(y, mo, d) if _valid(y, mo, d) else None
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if m:
        d, mo, y = map(int, m.groups())
        return period(y, mo, d) if _valid(y, mo, d) else None
    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})", text)
    if m:
        y, mo = map(int, m.groups())
        return period(y, mo, grain="month") if 1 <= mo <= 12 else None
    m = re.fullmatch(r"(\d{1,2})[-./](\d{4})", text)
    if m:
        mo, y = map(int, m.groups())
        return period(y, mo, grain="month") if 1 <= mo <= 12 else None
    m = re.fullmatch(r"(?:q|кв\.?\s*)?([1-4])\s*(?:q|кв\.?|квартал)?\s*[-/ ]?\s*(\d{4})", text)
    if m and ("q" in text or "кв" in text):
        q, y = map(int, m.groups())
        return period(y, (q - 1) * 3 + 1, grain="quarter")
    m = re.fullmatch(r"(\d{4})\s*[-/ ]?\s*(?:q|кв\.?\s*)([1-4])", text)
    if m:
        y, q = map(int, m.groups())
        return period(y, (q - 1) * 3 + 1, grain="quarter")
    m = re.fullmatch(r"([a-zа-яё]+)\.?\s+(\d{4})(?:\s*г\.?)?", text)
    if m:
        name, y = m.group(1), int(m.group(2))
        mo = _MONTHS_RU.get(name[:3]) or _MONTHS_EN.get(name[:3])
        return period(y, mo, grain="month") if mo else None
    m = re.fullmatch(r"(\d{4})(?:\s*г\.?)?", text)
    if m:
        y = int(m.group(1))
        return period(y, grain="year") if 1900 <= y <= 2100 else None
    return None


def _valid(y: int, mo: int, d: int) -> bool:
    try:
        date(y, mo, d)
    except ValueError:
        return False
    return 1900 <= y <= 2100


def fmt_number(value: float, *, decimals: int | None = None) -> str:
    """Русское форматирование: «1 234,5», «12», «0,35»."""
    if decimals is None:
        a = abs(value)
        decimals = 0 if a >= 100 or float(value).is_integer() else 1 if a >= 10 else 2
    text = f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text.replace(" ", " ")


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """Согласование с числом: 1 точка, 2 точки, 5 точек."""
    n10, n100 = abs(n) % 10, abs(n) % 100
    if n10 == 1 and n100 != 11:
        return forms[0]
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return forms[1]
    return forms[2]


def fmt_scaled(value: float, unit: str | None = None) -> str:
    """Число с масштабом: 12 345 678 ₽ → «12,3 млн ₽». Единица с масштабом
    в шапке («млн ₽») не масштабируется повторно."""
    a = abs(value)
    if unit and re.match(r"^(тыс\.?|млн|млрд)(\s|$)", unit):
        return f"{fmt_number(value)}\u00a0{unit}"
    for limit, word in ((1e9, "млрд"), (1e6, "млн"), (1e3, "тыс.")):
        if a >= limit * 10 or (a >= limit and word != "тыс."):
            text = f"{fmt_number(value / limit, decimals=1)} {word}"
            break
    else:
        text = fmt_number(value)
    if unit == "%":
        return f"{text}%"
    return f"{text} {unit}" if unit else text


def fmt_ratio(ratio: float) -> str:
    return fmt_number(ratio, decimals=1)


def fmt_signed_pct(value: float, decimals: int = 0) -> str:
    """«+5%» / «−79%»: знак всегда один (никаких «+-79%»)."""
    if value < 0:
        return f"−{fmt_number(abs(value), decimals=decimals)}%"
    return f"+{fmt_number(value, decimals=decimals)}%"


def times_phrase(ratio: float) -> str:
    """«в 2,1 раза», «в 2 раза», «в 5 раз»: целое ≥5 — «раз»,
    дробное — всегда «раза»."""
    if abs(ratio - round(ratio)) < 0.05:
        n = int(round(ratio))
        return f"в {n} {plural(n, ('раз', 'раза', 'раз'))}"
    return f"в {fmt_number(ratio, decimals=1)} раза"


_ADJ_END = ("ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие")
_F_SOFT = {"прибыль", "сеть", "цепь", "тень"}


def head_gender(metric: str) -> str:
    """Род/число по главному существительному метки: m / f / n / p.
    «Средний чек» → m, «Новые клиенты» → p, «Число записей» → n,
    «Выручка B2B» → f, латиница (NPS, ROI) → m."""
    words = [w for w in re.findall(r"[A-Za-zА-Яа-яЁё]+", metric)]
    head = next(
        (w for w in words if not w.casefold().endswith(_ADJ_END)),
        words[0] if words else "",
    )
    low = head.casefold()
    if not low or not re.search(r"[а-яё]", low):
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
    """Глагол тренда, согласованный с метрикой: «Выручка выросла»."""
    return _VERBS[kind][head_gender(metric)]


STABLE_END = {"m": "ен", "f": "на", "n": "о", "p": "ы"}


__all__ = [
    "MONTH_NAMES_RU",
    "ParsedNumber",
    "ParsedPeriod",
    "STABLE_END",
    "fmt_number",
    "fmt_ratio",
    "fmt_scaled",
    "fmt_signed_pct",
    "head_gender",
    "parse_number",
    "parse_period",
    "period",
    "plural",
    "times_phrase",
    "verb",
]
