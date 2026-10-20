"""Выявление смысловых и статистических пробелов в скудном брифе (Gap Analysis)
и детерминированное/гибридное формулирование целевых поисковых запросов.

Когда пользователь передаёт короткий бриф (например: «Презентация для
инвесторов про сервис доставки готовой еды»), модуль:
1. Очищает тему от презентационной мета-лексики («презентация для инвесторов
   про...», «слайды о...», «питч-дек...») до чистого предметного ядра.
2. Проверяет существующий ContentPack по трём фундаментальным столпам
   бизнес-презентации:
   - ``market_trends``: объём рынка (TAM/SAM/SOM), динамика и CAGR за 3 года;
   - ``competitive_landscape``: 3–4 лидера сегмента, их доли и бизнес-модели;
   - ``unit_economics``: средний чек (AOV), стоимость привлечения (CAC), LTV,
     маржинальность и отраслевые бенчмарки.
3. Генерирует 2–3 снайперских поисковых запроса с географией, целевыми годами
   (2025–2026) и аналитическими маркерами за <1 мс (используются как
   структурная подсказка для LLM и как мгновенный fallback без деградации
   в мусорный запрос «презентация для инвесторов»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import Brief


class GapDimension(StrEnum):
    market_trends = "market_trends"
    competitive_landscape = "competitive_landscape"
    unit_economics = "unit_economics"


@dataclass(frozen=True)
class ResearchSubject:
    name: str
    kind: str
    aliases: tuple[str, ...]

    def matches(self, text: str) -> bool:
        return any(
            re.search(
                r"(?<!\w)"
                + re.escape(alias).replace(r"\ ", r"[\s._-]+")
                + (r"(?:а|е|у|ом|ы|и|ов)?" if re.search(r"[а-яё]$", alias, re.I) else "")
                + r"(?!\w)",
                text,
                re.IGNORECASE,
            )
            for alias in self.aliases
        )


def extract_research_subject(purpose: str) -> ResearchSubject:
    """Имя сохраняется целиком; транслитерация — поисковый вариант, не юрлицо."""
    core = extract_core_subject(purpose)
    name = re.split(r"\s+[—–-]\s+|[.!?]\s", core, maxsplit=1)[0]
    name = re.sub(
        r"^(?:аналитика|анализ|итоги года|компани[яи]|продукт|инициатива)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip(' «»"')
    kind = "market"
    if not re.match(r"(?:рынок|рынка|сервис|доставк|платформ|market)\b", name, re.I):
        if (
            len(name.split()) <= 3
            and (len(name.split()) == 1 or len(re.findall(r"\b[A-ZА-ЯЁ]", name)) >= 2)
            and not name.casefold().startswith("рынок")
        ):
            kind = "entity"
    aliases = [name]
    if re.fullmatch(r"[A-Z]{2,6}", name):
        # Общая транслитерация сокращений; нет справочника известных компаний.
        variant = name.translate(str.maketrans("ABVGDEZIYKLMNOPRSTUFH", "АБВГДЕЗИЙКЛМНОПРСТУФХ"))
        if variant != name:
            aliases.append(variant)
    return ResearchSubject(name=name, kind=kind, aliases=tuple(aliases))


@dataclass(frozen=True)
class DimensionGap:
    dimension: GapDimension
    title_ru: str
    title_en: str
    missing_metrics: tuple[str, ...]


@dataclass(frozen=True)
class BriefGapAnalysis:
    core_subject: str
    market_subject: str
    missing_dimensions: list[DimensionGap] = field(default_factory=list)
    covered_dimensions: list[GapDimension] = field(default_factory=list)
    targeted_queries: list[str] = field(default_factory=list)

    def to_prompt_list(self, language: str = "ru") -> list[dict[str, object]]:
        is_ru = language.startswith("ru")
        return [
            {
                "dimension": g.dimension.value,
                "title": g.title_ru if is_ru else g.title_en,
                "missing_metrics": list(g.missing_metrics),
            }
            for g in self.missing_dimensions
        ]


_META_PREFIX_RE = re.compile(
    r"^(?:"
    r"(?:сделать|создать|подготовить|собрать|нужна|требуется|разработать)\s+"
    r")?"
    r"(?:"
    r"презентаци[яюией]\b|питч(?:-дек|\s+дек)?\b|слайд(?:ы|ов)?\b|доклад\b|"
    r"обзор\b|отч[её]т\b|стратеги[яю]\b|предложени[ея]\b|кейс\b|"
    r"pitch\s+deck\b|presentation\b|slides?\b|deck\b|report\b"
    r")"
    r"(?:\s+(?:для|перед|в|на)\s+(?:инвестор\w*|руководств\w*|совет\w*\s+директоров|"
    r"клиент\w*|партн[её]р\w*|команд\w*|комитет\w*|жюри|фонд\w*|"
    r"investors?|board|executives?|clients?|management))?"
    r"(?:\s+(?:про|о|об|по\s+теме|на\s+тему|по\s+проекту|по|для|about|on|for))?\s*",
    re.IGNORECASE,
)

_LEAD_VERB_RE = re.compile(
    r"^(?:рассказать|показать|описать|представить|обосновать)\s+"
    r"(?:инвесторам\s+|руководству\s+)?"
    r"(?:про|о|об|по)?\s*",
    re.IGNORECASE,
)

_AUDIENCE_TAIL_RE = re.compile(
    r"\s+(?:для\s+(?:инвестор\w*|руководств\w*|совет\w*\s+директоров|"
    r"клиент\w*|партн[её]р\w*|венчурн\w+\s+фонд\w*|раунда\s+[a-zа-я0-9-]+)|"
    r"for\s+(?:investors?|board|executives?|seed\s+round|series\s+[a-c]))\s*$",
    re.IGNORECASE,
)

_SERVICE_WRAPPER_RE = re.compile(
    r"^(?:сервис\w*|платформ\w*|приложени\w*| агрегатор\w*|система|проект|стартап)\s+"
    r"(?:для\s+|по\s+)?(?=[а-яёa-z]{4,})",
    re.IGNORECASE,
)

_MARKET_TRENDS_RE = re.compile(
    r"\b(?:tam|sam|som|cagr|объ[её]м\w*\s+рынка|ёмкост\w*\s+рынка|емкост\w*\s+рынка|"
    r"рынок\s+(?:составил|оценивается|вырос|достиг)|темп\w*\s+роста|"
    r"market\s+size|market\s+volume|annual\s+growth)\b",
    re.IGNORECASE,
)

_COMPETITORS_RE = re.compile(
    r"\b(?:конкурент\w*|лидер\w*\s+(?:рынка|сегмента|отрасли)|доля\s+рынка|"
    r"рыночн\w+\s+дол\w+|ключев\w+\s+игрок\w*|топ-[345]|"
    r"competitor\w*|market\s+share|key\s+player\w*|market\s+leader\w*)\b",
    re.IGNORECASE,
)

_UNIT_ECONOMICS_RE = re.compile(
    r"\b(?:средн\w+\s+чек\w*|aov|cac|ltv|стоимост\w+\s+привлечения|"
    r"юнит-экономик\w*|unit[- ]economics|маржинальност\w*|рентабельност\w*|"
    r"конверси\w+|retention|удержани\w+|arpu|ebitda|take\s+rate|частот\w+\s+заказ\w*)\b",
    re.IGNORECASE,
)

_CATALOG: dict[GapDimension, DimensionGap] = {
    GapDimension.market_trends: DimensionGap(
        dimension=GapDimension.market_trends,
        title_ru="Рынок и тренды",
        title_en="Market size and trends",
        missing_metrics=(
            "объём рынка (TAM/SAM/SOM) в денежном выражении",
            "среднегодовой темп роста (CAGR) за последние 3 года",
            "ключевые драйверы и тренды сегмента 2025–2026",
        ),
    ),
    GapDimension.competitive_landscape: DimensionGap(
        dimension=GapDimension.competitive_landscape,
        title_ru="Конкурентный ландшафт",
        title_en="Competitive landscape",
        missing_metrics=(
            "3–4 лидера сегмента и их рыночные доли",
            "ключевые особенности бизнес-моделей и позиционирования игроков",
            "барьеры входа и консолидация рынка",
        ),
    ),
    GapDimension.unit_economics: DimensionGap(
        dimension=GapDimension.unit_economics,
        title_ru="Бизнес-метрики сегмента",
        title_en="Segment unit economics and benchmarks",
        missing_metrics=(
            "средний чек (AOV) и частота покупок в сегменте",
            "стоимость привлечения клиента (CAC) и пожизненная ценность (LTV)",
            "отраслевые бенчмарки маржинальности и удержания",
        ),
    ),
}


def extract_core_subject(purpose: str, language: str = "ru") -> str:
    """Извлекает предметную область из формулировки брифа без слов «презентация для...»."""
    raw = re.sub(r"\s+", " ", (purpose or "")).strip().strip("«»\"'.,;:!?")
    if not raw:
        return "отрасли" if language.startswith("ru") else "industry"
    cleaned = _META_PREFIX_RE.sub("", raw).strip()
    cleaned = _LEAD_VERB_RE.sub("", cleaned).strip()
    cleaned = _AUDIENCE_TAIL_RE.sub("", cleaned).strip(" —–-:.,;!?")
    return cleaned if len(cleaned) >= 3 else raw


def extract_market_subject(core_subject: str) -> str:
    """Формирует естественное дополнение для словосочетания «рынок <...>».

    Пример: «сервис доставки готовой еды» → «доставки готовой еды».
    """
    stripped = re.sub(r"^рын(?:ок|ка)\s+", "", core_subject.strip(), flags=re.I)
    stripped = _SERVICE_WRAPPER_RE.sub("", stripped).strip()
    return stripped if len(stripped) >= 4 else core_subject.strip()


def _pack_corpus(pack: ContentPack | None) -> str:
    if pack is None:
        return ""
    parts: list[str] = []
    for section in pack.sections:
        if section.heading:
            parts.append(section.heading)
        for block in section.blocks:
            if block.text:
                parts.append(block.text)
            if block.items:
                parts.extend(i for i in block.items if i)
    for table in pack.tables:
        if table.title:
            parts.append(table.title)
        parts.extend(table.headers)
    return "\n".join(parts)


def is_meta_presentation_query(query: str) -> bool:
    """Отсекает запросы, ищущие советы «как сделать презентацию» вместо фактов по теме."""
    q = query.strip().casefold()
    return bool(
        re.match(
            r"^(?:презентаци[яю]|слайды|питч|доклад|как\s+сделать\s+презентацию|"
            r"presentation\s+for|pitch\s+deck\s+for)\b",
            q,
        )
    )


def build_targeted_queries(
    brief: Brief,
    pack: ContentPack | None = None,
    *,
    max_queries: int = 3,
    focus: str = "facts",
    years: str = "2025 2026",
) -> list[str]:
    """Строит 2–3 точных поисковых запроса по недостающим измерениям брифа."""
    lang = (brief.language or (pack.language if pack else "ru") or "ru")[:2].casefold()
    core = extract_core_subject(brief.purpose, lang)
    market_sub = extract_market_subject(core)
    subject = extract_research_subject(brief.purpose)
    if subject.kind == "entity":
        name = subject.name
        alternate = subject.aliases[-1]
        suffixes = (
            (
                f"{name} компания выручка прибыль",
                f"{alternate} количество магазинов сотрудники",
                f"{alternate} выручка по годам отчетность",
                f"{alternate} доля рынка конкуренты",
                f"{name} компания рынок объем динамика",
            )
            if lang == "ru"
            else (
                f"{name} revenue profit annual results",
                f"{name} stores employees headcount",
                f"{name} revenue 2022 2023 2024 2025 annual report",
                f"{name} market share competitors",
                f"{name} industry market size trends",
            )
        )
        return list(suffixes)[:max_queries]
    corpus = f"{brief.purpose}\n{_pack_corpus(pack)}"

    has_market = bool(_MARKET_TRENDS_RE.search(corpus))
    has_comp = bool(_COMPETITORS_RE.search(corpus))
    has_metrics = bool(_UNIT_ECONOMICS_RE.search(corpus))

    queries: list[str] = []
    if lang == "ru":
        q_market = f"рынок {market_sub} РФ {years} объем аналитика"
        q_comp = f"рынок {market_sub} лидеры доля конкуренты РФ 2025"
        q_metrics = (
            f"{core} средний чек LTV CAC рентабельность статистика"
            if focus == "numbers"
            else f"{core} средний чек LTV CAC метрики аналитика"
        )
    else:
        q_market = f"{market_sub} market size CAGR {years} report"
        q_comp = f"{market_sub} market share top players competitors 2025"
        q_metrics = f"{core} average order value CAC LTV unit economics benchmarks"

    if not has_market:
        queries.append(q_market)
    if not has_comp:
        queries.append(q_comp)
    if not has_metrics:
        queries.append(q_metrics)

    for fallback_q in (q_market, q_comp, q_metrics):
        if fallback_q not in queries:
            queries.append(fallback_q)

    cleaned: list[str] = []
    seen: set[str] = set()
    for q in queries:
        norm = re.sub(r"\s+", " ", q).strip()[:120]
        if norm.casefold() not in seen:
            seen.add(norm.casefold())
            cleaned.append(norm)
        if len(cleaned) >= max(1, max_queries):
            break
    return cleaned


def analyze_brief_gaps(
    pack: ContentPack | None,
    brief: Brief,
    *,
    max_queries: int = 3,
    focus: str = "facts",
) -> BriefGapAnalysis:
    """Выявляет недостающие аналитические блоки и формирует целевые запросы."""
    lang = (brief.language or (pack.language if pack else "ru") or "ru")[:2].casefold()
    core = extract_core_subject(brief.purpose, lang)
    market_sub = extract_market_subject(core)
    corpus = f"{brief.purpose}\n{_pack_corpus(pack)}"

    checks = (
        (GapDimension.market_trends, _MARKET_TRENDS_RE),
        (GapDimension.competitive_landscape, _COMPETITORS_RE),
        (GapDimension.unit_economics, _UNIT_ECONOMICS_RE),
    )
    missing: list[DimensionGap] = []
    covered: list[GapDimension] = []
    for dim, pattern in checks:
        if pattern.search(corpus):
            covered.append(dim)
        else:
            missing.append(_CATALOG[dim])

    queries = build_targeted_queries(brief, pack, max_queries=max_queries, focus=focus)
    return BriefGapAnalysis(
        core_subject=core,
        market_subject=market_sub,
        missing_dimensions=missing,
        covered_dimensions=covered,
        targeted_queries=queries,
    )


__all__ = [
    "BriefGapAnalysis",
    "DimensionGap",
    "GapDimension",
    "analyze_brief_gaps",
    "build_targeted_queries",
    "extract_core_subject",
    "extract_market_subject",
    "is_meta_presentation_query",
]
