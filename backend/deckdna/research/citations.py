"""Очистка веб-фрагментов от шума, распознавание авторитетных источников
и автоматическое формирование консалтинговых сносок.

Решает две задачи:
1. Детерминированная очистка HTML/текста от cookie-баннеров, рекламных
   врезок, призывов подписаться и навигационного мусора + приоритизация
   абзацев с числовой статистикой в пределах окна ``max_page_chars``.
2. Преобразование сырых URL/заголовков в канонические названия аналитических
   агентств и деловых изданий («Data Insight», «РБК Исследования рынков»,
   «TAdviser», «Frank RG», «INFOLine», «Росстат») и генерация сноски слайда:
   «Источник: Data Insight, РБК Исследования рынков 2025».
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from deckdna.contracts.content_pack import ExternalSource, Origin

MODEL_BENCHMARK_TITLE_RU = (
    "Оценка на базе открытых отраслевых бенчмарков (не подтверждено источником)"
)
MODEL_BENCHMARK_TITLE_EN = "Estimate based on open industry benchmarks (not confirmed by a source)"

MODEL_BENCHMARK_FOOTNOTE_RU = "Источник: оценка на базе открытых отраслевых бенчмарков"
MODEL_BENCHMARK_FOOTNOTE_EN = "Source: estimate based on open industry benchmarks"

KNOWN_PUBLISHERS: dict[str, str] = {
    "datainsight.ru": "Data Insight",
    "research.rbc.ru": "РБК Исследования рынков",
    "marketing.rbc.ru": "РБК Исследования рынков",
    "rbc.ru": "РБК",
    "trends.rbc.ru": "РБК Тренды",
    "tadviser.ru": "TAdviser",
    "tadviser.com": "TAdviser",
    "frankrg.com": "Frank RG",
    "infoline.spb.ru": "INFOLine",
    "rosstat.gov.ru": "Росстат",
    "gks.ru": "Росстат",
    "cbr.ru": "Банк России",
    "kommersant.ru": "Коммерсантъ",
    "vedomosti.ru": "Ведомости",
    "forbes.ru": "Forbes",
    "cnews.ru": "CNews Analytics",
    "wciom.ru": "ВЦИОМ",
    "nafi.ru": "НАФИ",
    "businesstat.ru": "BusinesStat",
    "akisphera.ru": "АКИТ",
    "akit.ru": "АКИТ",
    "raexpert.ru": "Эксперт РА",
    "statista.com": "Statista",
    "mckinsey.com": "McKinsey & Company",
    "gartner.com": "Gartner",
    "bcg.com": "BCG",
    "bain.com": "Bain & Company",
}

_BOILERPLATE_RE = re.compile(
    r"(?:"
    r"мы\s+используем\s+файлы\s+cookie|"
    r"использует\s+cookie|"
    r"политик[ауой]\s+(?:конфиденциальности|обработки\s+персональных\s+данных)|"
    r"согласие\s+на\s+обработку\s+персональных\s+данных|"
    r"подпишитесь\s+на\s+(?:наш\s+)?(?:telegram|телеграм|рассылку|канал)|"
    r"читайте\s+также\s*:|"
    r"все\s+права\s+защищены|"
    r"материалы\s+сайта\s+предназначены\s+для\s+лиц\s+старше\s+18|"
    r"войти\s+в\s+личный\s+кабинет|"
    r"оформить\s+подписку\s+на\s+издание|"
    r"accept\s+all\s+cookies|"
    r"privacy\s+policy\s+and\s+terms\s+of\s+service|"
    r"subscribe\s+to\s+our\s+newsletter"
    r")",
    re.IGNORECASE,
)

_STAT_SIGNAL_RE = re.compile(
    r"(?:\d+(?:[.,]\d+)?\s*(?:%|процент\w*|трлн|млрд|млн|тыс|₽|руб\w*|\$|€|"
    r"billion|million|cagr|раз\b)|"
    r"\b(?:tam|sam|som|cagr|ltv|cac|aov|arpu|ebitda|доля\s+рынка|объ[её]м\s+рынка|"
    r"средний\s+чек)\b)",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b(202[2-9]|203\d)\b")
_TITLE_SPLIT_RE = re.compile(r"\s+(?:—|–|\||::)\s+")


def is_boilerplate_line(line: str) -> bool:
    """Является ли строка техническим/навигационным мусором веб-страницы."""
    text = line.strip()
    if len(text) < 25:
        return True
    if _BOILERPLATE_RE.search(text):
        return True
    # Строки, состоящие почти целиком из пунктов меню через «|» или «•»
    return text.count("|") >= 4 or text.count("•") >= 5


def clean_boilerplate_lines(lines: list[str]) -> list[str]:
    """Удаляет дубли и мусорные блоки (cookie, подписки, меню), сохраняя порядок."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        line = re.sub(r"\s+", " ", raw).strip()
        if is_boilerplate_line(line):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(line)
    return cleaned


def select_page_excerpt(text: str, max_chars: int = 6000) -> str:
    """Формирует срез текста страницы до ``max_chars`` символов без потери цифр.

    Если статья длиннее ``max_chars``, в окно гарантированно включаются
    абзацы со статистикой (числа, валюты, проценты, CAGR, доли рынка) с
    сохранением их исходного порядка следования в документе.
    """
    if len(text) <= max_chars:
        return text
    paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    if len(paragraphs) <= 1:
        return text[:max_chars]

    scored: list[tuple[int, int, str]] = []
    for idx, para in enumerate(paragraphs):
        signals = len(_STAT_SIGNAL_RE.findall(para))
        has_year = 1 if _YEAR_RE.search(para) else 0
        # Первые 2 абзаца (лид статьи) и абзацы с цифрами получают высший приоритет
        priority = (signals * 3 + has_year) if signals > 0 else (2 if idx < 2 else 0)
        scored.append((priority, idx, para))

    by_priority = sorted(scored, key=lambda item: (-item[0], item[1]))
    chosen_indices: list[int] = []
    used_chars = 0
    for _prio, idx, para in by_priority:
        cost = len(para) + (1 if chosen_indices else 0)
        if used_chars + cost > max_chars:
            continue
        chosen_indices.append(idx)
        used_chars += cost

    if not chosen_indices:
        return text[:max_chars]
    chosen_indices.sort()
    return "\n".join(paragraphs[i] for i in chosen_indices)[:max_chars]


def resolve_publisher_name(url: str | None, page_title: str | None = None) -> str:
    """Определяет человекочитаемое название источника по URL и заголовку страницы."""
    title = (page_title or "").strip()
    host = (urlparse(url).hostname or "").casefold() if url else ""
    bare_host = host[4:] if host.startswith("www.") else host

    # Особый случай РБК: если в заголовке или URL упоминаются исследования рынков
    if bare_host == "rbc.ru" and re.search(
        r"исследован\w*\s+рынк\w*|аналитик\w*\s+рбк|рбк\s+исследован\w*",
        title,
        re.IGNORECASE,
    ):
        return "РБК Исследования рынков"

    if bare_host in KNOWN_PUBLISHERS:
        return KNOWN_PUBLISHERS[bare_host]
    for domain, publisher in KNOWN_PUBLISHERS.items():
        if bare_host.endswith("." + domain):
            return publisher

    # Если заголовок имеет вид «Статья о рынке — Data Insight»
    if title:
        parts = _TITLE_SPLIT_RE.split(title)
        if len(parts) >= 2:
            tail = parts[-1].strip()
            for publisher in KNOWN_PUBLISHERS.values():
                if publisher.casefold() in tail.casefold():
                    return publisher
            if 2 <= len(tail) <= 35 and not any(ch.isdigit() for ch in tail):
                return tail

    return host or "Источник"


def extract_publication_year(*texts: str | None, default_year: str = "2025") -> str:
    """Извлекает наиболее актуальный год (2022–2029) из заголовка или URL."""
    years: list[str] = []
    for text in texts:
        if text:
            years.extend(_YEAR_RE.findall(text))
    return max(years) if years else default_year


def format_source_footnote(
    sources: list[ExternalSource],
    *,
    language: str = "ru",
    default_year: str = "2025",
) -> str:
    """Формирует компактную сноску для слайда по стандарту консалтинга.

    Примеры:
    - веб-источники: «Источник: Data Insight, РБК Исследования рынков 2025»
    - только знания модели: «Источник: оценка на базе открытых отраслевых бенчмарков»
    """
    is_ru = language.startswith("ru")
    web_sources = [s for s in sources if s.origin == Origin.web]
    if not web_sources:
        has_model = any(s.origin == Origin.model for s in sources)
        if has_model:
            return MODEL_BENCHMARK_FOOTNOTE_RU if is_ru else MODEL_BENCHMARK_FOOTNOTE_EN
        return ""

    publishers: list[str] = []
    seen: set[str] = set()
    doc_years: list[str] = []
    query_years: list[str] = []
    for src in web_sources:
        pub = src.publisher or resolve_publisher_name(src.url, src.title)
        if pub in KNOWN_PUBLISHERS:
            pub = KNOWN_PUBLISHERS[pub]
        elif pub.startswith("www.") and pub[4:] in KNOWN_PUBLISHERS:
            pub = KNOWN_PUBLISHERS[pub[4:]]
        else:
            resolved = resolve_publisher_name(src.url, src.title)
            if resolved in KNOWN_PUBLISHERS.values():
                pub = resolved
        key = pub.casefold()
        if pub and key not in seen:
            seen.add(key)
            publishers.append(pub)
        for text in (src.title, src.url):
            if text:
                doc_years.extend(_YEAR_RE.findall(text))
        if src.query:
            query_years.extend(_YEAR_RE.findall(src.query))

    year = max(doc_years) if doc_years else (max(query_years) if query_years else default_year)
    joined = ", ".join(publishers[:3])
    prefix = "Источник" if is_ru else "Source"
    if year and year not in joined:
        return f"{prefix}: {joined} {year}"
    return f"{prefix}: {joined}"


__all__ = [
    "KNOWN_PUBLISHERS",
    "MODEL_BENCHMARK_FOOTNOTE_EN",
    "MODEL_BENCHMARK_FOOTNOTE_RU",
    "MODEL_BENCHMARK_TITLE_EN",
    "MODEL_BENCHMARK_TITLE_RU",
    "clean_boilerplate_lines",
    "extract_publication_year",
    "format_source_footnote",
    "is_boilerplate_line",
    "resolve_publisher_name",
    "select_page_excerpt",
]
