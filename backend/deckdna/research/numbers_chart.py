"""Нативный график из найденных цифр (ADR-032 §5).

Ресёрч слайда с ``focus=numbers`` приносит факты с цифрами, каждый с
дословной цитатой со страницы. Если цифры однородны — одна единица измерения
и сопоставимые категории (годы или подписи) — из них собирается ``Chart``:
вёрстка ставит его нативным редактируемым графиком (``chart_fill`` или
create-fallback). Разнородные цифры графиком не делаются: из них — таблица
«показатель — значение». Подписи осей и единица берутся из самих фактов, а
источник каждого факта уже стоит на слайде источников.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from deckdna.contracts.content_pack import Chart, ChartSeries, SourceRef, Table
from deckdna.contracts.dataset import Dataset, DatasetColumn
from deckdna.research.gaps import ResearchSubject

MIN_CHART_POINTS = 3
_MAX_LABEL_WORDS = 5

_NUMBER_RE = re.compile(
    r"(?<![\w.,])(?P<num>\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"\s*(?P<unit>%|процент\w*|трлн\.?|млрд\.?|млн\.?|тыс\.?|₽|руб\w*\.?|\$|долл\w*|€|евро"
    r"|чел\w*|раз[а]?\b|billion|million|thousand|percent)?"
    r"(?:\s*(?P<cur>₽|руб\w*\.?|\$|долл\w*|€|евро))?",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2}|21\d{2})\b")
_YEAR_WORDS_RE = re.compile(r"\b(?:году|года|год|гг?\.|по итогам)\b", re.IGNORECASE)
_LEAD_RE = re.compile(r"^(?:в|во|за|к|на|по|с)\s+", re.IGNORECASE)
_UNIT_CANON = {
    "триллион": "трлн",
    "миллиард": "млрд",
    "миллион": "млн",
    "тысяч": "тыс.",
    "процент": "%",
    "percent": "%",
    "трлн": "трлн",
    "млрд": "млрд",
    "billion": "млрд",
    "млн": "млн",
    "million": "млн",
    "тыс": "тыс.",
    "thousand": "тыс.",
    "руб": "₽",
    "долл": "$",
    "евро": "€",
    "чел": "чел.",
    "раз": "раз",
}
_LABEL_TAIL_RE = re.compile(
    r"\s*(?:составил[аио]?|составляет|составят|достиг\w*|вырос\w*|вырастет|вырастут|"
    r"увеличил\w*|сократил\w*|снизил\w*|равн\w*|—|–|-|:|около|более|менее|до|на|в)\s*$",
    re.IGNORECASE,
)


def _unit(raw: str | None) -> str:
    if not raw:
        return ""
    raw = raw.casefold().rstrip(".")
    for prefix, canon in _UNIT_CANON.items():
        if raw.startswith(prefix):
            return canon
    return raw


def _value(raw: str) -> float:
    return float(raw.replace(" ", "").replace(" ", "").replace(",", "."))


@dataclass(frozen=True)
class NumericFact:
    text: str
    value: float
    unit: str
    year: str | None
    label: str


def parse_fact(text: str) -> NumericFact | None:
    """Одно значение с единицей (год не считается значением) или None."""
    years = _YEAR_RE.findall(text)
    values = []
    for m in _NUMBER_RE.finditer(text):
        if m.group("num") in years and not m.group("unit"):
            continue
        values.append(m)
    if len(values) != 1:
        return None
    m = values[0]
    head = text[: m.start()]
    for year in years:
        head = head.replace(year, " ")
    head = _YEAR_WORDS_RE.sub(" ", head)
    head = _LEAD_RE.sub("", head.strip())
    previous = None
    while previous != head:  # «выросла до» → «выросла» → «»
        previous = head
        head = _LABEL_TAIL_RE.sub("", head.strip(" ,;:"))
    words = head.split()
    label = " ".join(words[-_MAX_LABEL_WORDS:]).strip(" ,;:—–-") if words else ""
    return NumericFact(
        text=text,
        value=_value(m.group("num")),
        unit=" ".join(u for u in (_unit(m.group("unit")), _unit(m.group("cur"))) if u),
        year=years[0] if len(set(years)) == 1 else None,
        label=label[:1].upper() + label[1:] if label else "",
    )


def chart_or_table(
    facts: list[str], item_id: str, source_ref: SourceRef
) -> tuple[Chart | None, Table | None]:
    """График из однородных цифр, таблица из разнородных, иначе (None, None)."""
    parsed = [p for p in (parse_fact(f) for f in facts) if p is not None]
    if len(parsed) < 2:
        return None, None
    units = {p.unit for p in parsed}
    if len(parsed) >= MIN_CHART_POINTS and len(units) == 1:
        unit = units.pop()
        years = [p.year for p in parsed]
        if (
            all(years)
            and len(set(years)) == len(years)
            and len({p.label.casefold() for p in parsed}) == 1
        ):
            points = sorted(parsed, key=lambda p: p.year or "")
            categories = [p.year or "" for p in points]
            name = parsed[0].label or (unit or "Значение")
        elif not all(years):
            points = parsed
            categories = [p.label for p in points]
            name = unit or "Значение"
        else:
            points, categories, name = [], [], ""
        if categories and all(categories) and len(set(categories)) == len(categories):
            series_name = f"{name}, {unit}" if unit and unit not in name else name
            return Chart(
                id=item_id,
                categories=categories,
                series=[ChartSeries(name=series_name, values=[p.value for p in points])],
                source_ref=source_ref,
            ), None
    rows = [[p.label or p.text, f"{_fmt(p.value)} {p.unit}".strip()] for p in parsed]
    return None, Table(
        id=item_id,
        headers=["Показатель", "Значение"],
        rows=rows,
        source_ref=source_ref,
    )


def _fmt(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}".replace(".", ",")


__all__ = ["MIN_CHART_POINTS", "NumericFact", "chart_or_table", "parse_fact"]


@dataclass(frozen=True)
class GroundedNumber:
    """Число из скачанного текста. Период показателя не равен дате публикации."""

    entity: str
    metric: str
    value: float
    unit: str
    period: str | None
    source_url: str
    publisher: str
    published_at: str | None
    quote: str
    text: str
    qualifier: str = "exact"
    scope: str = ""


# Более специфичные метрики стоят раньше: «чистая прибыль» не равна прибыли.
_METRICS = (
    ("Чистая прибыль", r"чист\w*\s+прибыл\w*"),
    ("Чистый убыток", r"чист\w*\s+убыт\w*"),
    ("Валовая прибыль", r"валов\w*\s+прибыл\w*"),
    ("Прибыль от продаж", r"прибыл\w*\s+от\s+продаж"),
    ("Операционная прибыль", r"операционн\w*\s+прибыл\w*"),
    ("Коммерческие расходы", r"коммерческ\w*\s+расход\w*"),
    ("Управленческие расходы", r"управленческ\w*\s+расход\w*"),
    ("Капитальные затраты", r"капитальн\w*\s+затрат\w*|capex"),
    ("Себестоимость", r"себестоимост\w*"),
    ("Расходы", r"расход\w*|издержк\w*|затрат\w*|потратил\w*\s+на\s+\w+"),
    ("EBITDA", r"\bebitda\b"),
    ("Выручка", r"выручк\w*|онлайн-выручк\w*|онлайн-продаж\w*|revenue"),
    ("Прибыль", r"прибыл\w*|profit"),
    ("Убыток", r"убыт\w*"),
    ("GMV", r"\bgmv\b"),
    ("Оборот", r"оборот\w*"),
    (
        "Магазины",
        r"(?:количеств\w*\s+|числ\w*\s+)?магазин\w*|торгов\w*\s+точ\w*"
        r"|пвз|пункт\w*\s+выдач\w*|точ(?:ек|ки|ка)\b",
    ),
    (
        "Сотрудники",
        r"(?:(?:численност\w*|количеств\w*|числ\w*|штат\w*)\s+)?сотрудников"
        r"|персонал\w*|занят\w*|задействован\w*",
    ),
    ("Доля рынка", r"дол[яию]\w*(?:\s+рынка)?|market share"),
    ("Объём рынка", r"объ[её]м\w*\s+рынка|рынок\w*|market size"),
    ("Средний чек", r"средн\w*\s+чек\w*"),
    (
        "Активные покупатели",
        r"(?:активн\w*\s+)?покупател\w*|(?:активн\w*\s+|покупа\w*\s+)клиент\w*|клиент\w*",
    ),
    ("Активные продавцы", r"(?:активн\w*\s+)?продавц\w*"),
    ("Заказы", r"(?:количеств\w*\s+|числ\w*\s+)?заказ\w*"),
    ("Поездки", r"(?:количеств\w*\s+|числ\w*\s+)?поезд\w*"),
)
_MEASURE_RE = re.compile(
    r"(?<![\w.,])(?P<num>-?\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?)"
    r"\s*(?P<unit>%|процент\w*|трлн\.?|млрд\.?|млн\.?|тыс\.?|₽|руб\w*\.?|\$|долл\w*\.?|€|евро"
    r"|человек|чел\.?|сотрудник\w*|магазин\w*|пвз|точ(?:ек|ки|ка)\b|клиент\w*|покупател\w*"
    r"|продавц\w*|заказ\w*|поезд\w*|billion|million"
    r"|триллион\w*|миллиард\w*|миллион\w*|тысяч\w*)?"
    r"(?:\s*(?P<cur>₽|руб\w*\.?|\$|долл\w*\.?|€|евро|человек))?",
    re.I,
)
_SENTENCE_LEAD_WORDS_RE = re.compile(
    r"^\s*(?:по|в|во|за|на|с|со|к|у|от|до|из|при|помимо|кроме|согласно|однако|также|"
    r"всего|итого|сегодня|ранее|сейчас|теперь|объем|объём|число|количество|численность|"
    r"рост|снижение|динамика|сеть|компания|ритейлер|маркетплейс|сервис|группа|холдинг|"
    r"показатель|показатели|итоги|результаты)\b",
    re.I,
)
_DELTA_BEFORE_NUM_RE = re.compile(
    r"(?:(?:вырос\w*|увеличил\w*|снизил\w*|сократил\w*|упал\w*|поднял\w*|прибавил\w*|"
    r"рост\w*|прирост\w*|снижени\w*|падени\w*|увеличени\w*|сокращени\w*)"
    r"(?:\s+[\w«»\"-]+){0,4}\s+на|против|по\s+сравнению\s+с)\s*$",
    re.I,
)
_LIST_HEADER_UNIT_RE = re.compile(
    r",\s*(?P<unit>трлн\.?|млрд\.?|млн\.?|тыс\.?)\s*(?P<cur>₽|руб\w*\.?|\$|долл\w*\.?|€|евро)\s*:\s*$",
    re.I,
)
_LIST_YEAR_ITEM_RE = re.compile(
    r"^(?P<yr>19\d{2}|20\d{2})\s*(?:год|г\.?)?\s*[—–-]\s*(?P<num>\d+(?:[.,]\d+)?)\s*[;.]?$",
    re.I,
)
_PAREN_SHARE_RE = re.compile(
    r"(?P<player>[A-ZА-ЯЁ][\w«»\"-]+(?:\s+[A-ZА-ЯЁa-zа-яё][\w«»\"-]+)?)\s*"
    r"\(\s*(?P<num>\d+(?:[.,]\d+)?)\s*%\s*\)"
)
_MONEY_METRICS = {
    "Выручка",
    "Прибыль",
    "Убыток",
    "Чистая прибыль",
    "Чистый убыток",
    "Валовая прибыль",
    "Прибыль от продаж",
    "Операционная прибыль",
    "Коммерческие расходы",
    "Управленческие расходы",
    "Капитальные затраты",
    "Себестоимость",
    "Расходы",
    "EBITDA",
    "GMV",
    "Оборот",
    "Объём рынка",
    "Средний чек",
}
_COUNT_METRICS = {
    "Магазины",
    "Сотрудники",
    "Заказы",
    "Поездки",
    "Активные покупатели",
    "Активные продавцы",
}


def _has_foreign_owner(
    region: str,
    subject: ResearchSubject,
    metric_matches: list[tuple[int, int, str]],
    *,
    strip_lead: bool,
) -> bool:
    owner_region = region
    if strip_lead:
        for m_start, m_end, _ in metric_matches:
            if m_start < len(owner_region):
                end_idx = min(m_end, len(owner_region))
                owner_region = (
                    owner_region[:m_start] + (" " * (end_idx - m_start)) + owner_region[end_idx:]
                )
        owner_region = _SENTENCE_LEAD_WORDS_RE.sub("", owner_region)
    owner = re.sub(
        r'[«"]([^»"]+)[»"]',
        lambda m: "" if subject.matches(m.group(1)) else m.group(0),
        owner_region,
    )
    for alias in subject.aliases:
        if len(alias) < 2:
            continue
        owner = re.sub(
            re.escape(alias).replace(r"\ ", r"[\s._-]+")
            + (r"(?:а|е|у|ом|ы|и|ов)?" if re.search(r"[а-яё]$", alias, re.I) else ""),
            "",
            owner,
            flags=re.I,
        )
    # Географические локации («в России», «в Казахстане», «во Владивостоке») не меняют субъект.
    owner = re.sub(r"\b(?:в|во|из)\s+[А-ЯЁ][а-яё-]+", "", owner)
    owner = re.sub(
        r"\b(?:ООО|ПАО|АО|МКПАО|ЗАО|ОАО|РСБУ|МСФО|EBITDA|GAAP|GMV|ПВЗ|РФ|России|Россия)\b",
        "",
        owner,
    )
    return bool(re.search(r"[А-ЯЁA-Z][а-яёa-zА-ЯЁA-Z]+", owner))


def extract_grounded_numbers(
    text: str,
    subject: ResearchSubject,
    url: str,
    publisher: str,
    published_at: str | None = None,
) -> list[GroundedNumber]:
    """Консервативное извлечение: объект и метрика в одном предложении.

    Сложные сравнения, диапазоны, прогнозы и неоднозначные периоды не
    превращаются в ряд. Единицы не пересчитываются без явного масштаба.
    """
    out: list[GroundedNumber] = []
    seen: set[tuple] = set()
    # Не режем десятичную точку, млрд. и руб.; не склеиваем соседние абзацы.
    sentences = re.split(r"\n+|(?<=[!?;])\s+|(?<=[а-яa-z%\d]\.)\s+(?=[А-ЯA-Z«])", text)
    entity_context = ""
    anchor_sentence = ""
    dedicated_entity_page = subject.kind == "entity" and subject.matches(text[:400])
    list_state: tuple[str, str, str] | None = None  # (metric, unit, accumulated_quote)
    for sentence in sentences:
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if not 10 <= len(sentence) <= 700:
            list_state = None
            continue
        if list_state is not None:
            m_item = _LIST_YEAR_ITEM_RE.match(sentence)
            if m_item:
                l_metric, l_unit, l_quote = list_state
                l_quote = f"{l_quote}\n{sentence}"
                if len(l_quote) <= 800:
                    list_state = (l_metric, l_unit, l_quote)
                    yr = m_item.group("yr")
                    val = float(m_item.group("num").replace(",", "."))
                    key = (subject.name, l_metric, val, l_unit, yr)
                    if key not in seen:
                        seen.add(key)
                        num_txt = m_item.group("num")
                        out.append(
                            GroundedNumber(
                                entity=subject.name,
                                metric=l_metric,
                                value=val,
                                unit=l_unit,
                                period=yr,
                                source_url=url,
                                publisher=publisher,
                                published_at=published_at,
                                quote=l_quote,
                                qualifier="exact",
                                scope=subject.name,
                                text=f"{subject.name} — {l_metric}: {num_txt} {l_unit} ({yr}).",
                            )
                        )
                    continue
            list_state = None
        if len(sentence) < 15:
            continue
        has_subject = subject.matches(sentence)
        if subject.kind == "entity":
            if has_subject:
                anchor_sentence = sentence
                entity_context = sentence
            elif anchor_sentence and len(entity_context) + len(sentence) < 800:
                entity_context += "\n" + sentence
            elif dedicated_entity_page and anchor_sentence and len(sentence) < 400:
                entity_context = sentence
            else:
                anchor_sentence = ""
                entity_context = ""
                continue
        else:
            words = re.findall(r"[а-яёa-z]{4,}", subject.name.casefold())
            words = [w[:4] for w in words if w not in {"рынок", "рынка", "россии", "россия"}]
            if words and not any(w in sentence.casefold() for w in words):
                continue
        if re.search(r"прогноз|ожида\w*|может|планиру\w*|forecast|к 20\d\d", sentence, re.I):
            continue
        # Другая организация в том же предложении требует семантического
        # разбора. Для автоматической статистики отказываемся от сравнения.
        if re.search(r"конкурент\w*|друг\w*\s+компани\w*|тогда как|в то время как", sentence, re.I):
            anchor_sentence = ""
            entity_context = ""
            continue
        metric_matches = []
        occupied: list[tuple[int, int]] = []
        for metric, pattern in _METRICS:
            for match in re.finditer(pattern, sentence, re.I):
                if any(start <= match.start() < end for start, end in occupied):
                    continue
                metric_matches.append((match.start(), match.end(), metric))
                occupied.append(match.span())
        metric_matches.sort()
        if subject.kind == "entity" and not has_subject:
            if _has_foreign_owner(sentence, subject, metric_matches, strip_lead=True):
                anchor_sentence = ""
                entity_context = ""
                continue
        header_unit_m = _LIST_HEADER_UNIT_RE.search(sentence)
        if header_unit_m and metric_matches:
            h_unit = " ".join(
                u
                for u in (_unit(header_unit_m.group("unit")), _unit(header_unit_m.group("cur")))
                if u
            )
            list_state = (metric_matches[0][2], h_unit, sentence)
            continue
        all_years = _YEAR_RE.findall(sentence)
        comparison = re.search(r"по сравнению с|относительно|годом ранее|к уровню", sentence, re.I)
        sentence_no_comp = re.sub(
            r"(?:(?:по сравнению с|относительно|к уровню)\s+(?:[\w-]+\s+){0,3}?|"
            r"(?:зарегистрирован\w*|основан\w*|создан\w*)\s+в\s+)(19\d{2}|20\d{2}|21\d{2})",
            "",
            sentence,
            flags=re.I,
        )
        years = _YEAR_RE.findall(sentence_no_comp)
        period = years[0] if len(set(years)) == 1 else None
        if not period and published_at and re.search(r"в прошлом году", sentence, re.I):
            period = str(int(published_at[:4]) - 1)
        if re.search(r"квартал|полугод|(?<!12\s)месяц|quarter|[Qq][1-4]", sentence, re.I):
            # Не смешиваем годовые показатели и неполный год.
            continue
        if subject.kind == "market":
            paren_shares = list(_PAREN_SHARE_RE.finditer(sentence))
            if len(paren_shares) >= 2:
                for pm in paren_shares:
                    player_name = pm.group("player").strip(' «»"')
                    val = float(pm.group("num").replace(",", "."))
                    key = (player_name, "Доля рынка", val, "%", period)
                    if key not in seen:
                        seen.add(key)
                        out.append(
                            GroundedNumber(
                                entity=player_name,
                                metric="Доля рынка",
                                value=val,
                                unit="%",
                                period=period,
                                source_url=url,
                                publisher=publisher,
                                published_at=published_at,
                                quote=sentence,
                                qualifier="exact",
                                scope=subject.name,
                                text=f"{player_name} — Доля рынка: {pm.group('num')} %"
                                + (f" ({period})." if period and period in sentence else "."),
                            )
                        )
                continue
        measures = []
        for match in _MEASURE_RE.finditer(sentence):
            if match.group("num") in all_years and not match.group("unit"):
                continue
            if not match.group("unit"):
                continue
            measures.append(match)
        multi_years = len(set(years)) > 1 and not comparison
        if multi_years and len(measures) < 2:
            continue
        prev_measure_end = 0
        for match in measures:
            measure_period = period
            if multi_years:
                clause_years = _YEAR_RE.findall(sentence_no_comp[prev_measure_end : match.start()])
                prev_measure_end = match.end()
                if len(set(clause_years)) != 1:
                    continue
                measure_period = clause_years[0]
            # Метрика-единица счёта («25 000 магазинов»), входящая в само число,
            # не должна становиться общей метрикой для соседних чисел в предложении.
            immediate_after = [
                m
                for m in metric_matches
                if m[2] in _COUNT_METRICS
                and match.start() <= m[0] <= match.end() + 2
                and not sentence[min(match.end(), m[0]) : m[0]].strip()
            ]
            free_metrics = [
                m
                for m in metric_matches
                if not any(
                    other is not match
                    and m[2] in _COUNT_METRICS
                    and other.start() <= m[0] <= other.end() + 2
                    for other in measures
                )
            ]
            preceding = [m for m in free_metrics if m[1] <= match.start()]
            nearest = (
                immediate_after[0]
                if immediate_after
                else preceding[-1]
                if preceding
                else next(iter(free_metrics), None)
            )
            if nearest is None:
                continue
            start, end, metric = nearest
            if subject.kind == "entity" and metric == "Объём рынка":
                continue
            if _DELTA_BEFORE_NUM_RE.search(sentence[: match.start()]):
                continue
            if subject.kind == "entity":
                owner_region = (
                    sentence[min(end, match.start()) : match.start()]
                    if has_subject
                    else sentence[: match.start()]
                )
                if _has_foreign_owner(
                    owner_region, subject, metric_matches, strip_lead=not has_subject
                ):
                    anchor_sentence = ""
                    entity_context = ""
                    continue  # число принадлежит другому именованному объекту
            entity = subject.name
            raw_unit = (match.group("unit") or "").casefold()
            if raw_unit in {"%"} and any(m[2] == "Доля рынка" for m in metric_matches):
                metric = "Доля рынка"
                if subject.kind == "market":
                    player = re.search(
                        r"доля\s+(?:компании\s+|сервиса\s+)?[«\"]?"
                        r"([A-ZА-ЯЁ][\w«»\" -]{1,30}?)[»\"]?\s+(?:на|в|состав)",
                        sentence,
                    )
                    if not player:
                        continue
                    entity = player.group(1).strip(' «»"')
            unit = " ".join(u for u in (_unit(raw_unit), _unit(match.group("cur"))) if u)
            if metric in _MONEY_METRICS:
                if raw_unit.startswith(("процент", "%")):
                    continue  # процент роста не является выручкой/оборотом
                if not any(c in unit for c in ("₽", "$", "€")):
                    continue  # «700 млрд» без валюты неоднозначно
            if metric == "Доля рынка" and unit != "%":
                continue
            if metric in _COUNT_METRICS and (
                unit == "%" or any(c in unit for c in ("₽", "$", "€"))
            ):
                continue
            if metric == "Магазины" and ("чел" in unit or "млн" in unit):
                continue
            # «от 10 до 20» нельзя превращать в два точных наблюдения.
            if re.search(r"\bот\s+\d|\d\s*[-–—]\s*\d", sentence):
                continue
            qualifier = (
                "approximate"
                if re.search(r"около|более|менее|свыше|порядка|примерно", sentence, re.I)
                else "exact"
            )
            value = float(re.sub(r"[ \u00a0\u202f]", "", match.group("num")).replace(",", "."))
            approximation = re.search(r"около|более|менее|свыше|порядка|примерно", sentence, re.I)
            prefix = approximation.group(0) + " " if approximation else ""
            key = (entity, metric, value, unit, measure_period)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                GroundedNumber(
                    entity=entity,
                    metric=metric,
                    value=value,
                    unit=unit,
                    period=measure_period,
                    source_url=url,
                    publisher=publisher,
                    published_at=published_at,
                    quote=entity_context if subject.kind == "entity" else sentence,
                    qualifier=qualifier,
                    scope=subject.name,
                    text=f"{entity} — {metric}: {prefix}{match.group('num')} {unit}"
                    + (
                        f" ({measure_period})."
                        if measure_period and measure_period in sentence
                        else "."
                    ),
                )
            )
    return out


def datasets_from_numbers(facts: list[GroundedNumber]) -> list[Dataset]:
    """≥3 сопоставимых годовых точек → Dataset; URL каждой строки в context_text."""
    import hashlib

    groups: dict[tuple[str, str, str], list[GroundedNumber]] = {}
    for fact in facts:
        if fact.period and fact.qualifier == "exact":
            groups.setdefault((fact.entity, fact.metric, fact.unit), []).append(fact)
    datasets = []
    for (entity, metric, unit), group in groups.items():
        by_year: dict[str, list[GroundedNumber]] = {}
        for fact in group:
            by_year.setdefault(fact.period, []).append(fact)
        # Конфликт источников за один год — не выбирать удобную цифру.
        points = [
            items[0] for _, items in sorted(by_year.items()) if len({p.value for p in items}) == 1
        ]
        if len(points) < MIN_CHART_POINTS:
            continue
        key = hashlib.sha1(f"{entity}|{metric}|{unit}".encode()).hexdigest()[:10]  # noqa: S324
        provenance = "\n".join(
            f"{p.period}: {p.source_url} — {p.publisher}; {p.quote}" for p in points
        )
        datasets.append(
            Dataset(
                id=f"web-dataset-{key}",
                title=f"{entity}: {metric}",
                row_count=len(points),
                columns=[
                    DatasetColumn(name="Год", values=[p.period for p in points]),
                    DatasetColumn(name=f"{metric}, {unit}", values=[p.value for p in points]),
                ],
                source_ref=SourceRef(
                    artifact_id="web:"
                    + hashlib.sha1(  # noqa: S324
                        points[0].source_url.split("#", 1)[0].rstrip("/").encode()
                    ).hexdigest()[:10]
                ),  # noqa: S324
                context_text=f"{entity}. {metric}, {unit}.\n{provenance}",
            )
        )
    shares: dict[tuple[str, str | None], list[GroundedNumber]] = {}
    for fact in facts:
        if fact.metric == "Доля рынка" and fact.unit == "%" and fact.qualifier == "exact":
            shares.setdefault((fact.scope, fact.period), []).append(fact)
    for (scope, period), group in shares.items():
        by_player: dict[str, list[GroundedNumber]] = {}
        for fact in group:
            by_player.setdefault(fact.entity, []).append(fact)
        points = [items[0] for items in by_player.values() if len({p.value for p in items}) == 1]
        if len(points) < MIN_CHART_POINTS or sum(p.value for p in points) > 100:
            continue
        key = hashlib.sha1(f"{scope}|shares|{period}".encode()).hexdigest()[:10]  # noqa: S324
        datasets.append(
            Dataset(
                id=f"web-dataset-{key}",
                title=f"{scope}: доли, {period or 'период не указан'}",
                row_count=len(points),
                columns=[
                    DatasetColumn(name="Игрок", values=[p.entity for p in points]),
                    DatasetColumn(name="Доля рынка, %", values=[p.value for p in points]),
                ],
                source_ref=SourceRef(
                    artifact_id="web:"
                    + hashlib.sha1(  # noqa: S324
                        points[0].source_url.split("#", 1)[0].rstrip("/").encode()
                    ).hexdigest()[:10]
                ),
                context_text="\n".join(f"{p.entity}: {p.source_url} — {p.quote}" for p in points),
            )
        )
    return datasets
