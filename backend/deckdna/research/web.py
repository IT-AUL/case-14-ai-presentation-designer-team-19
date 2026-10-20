"""Веб-ресёрч для добора контента (ADR-030 §3, шаги 1–4).

Поток: модель предлагает поисковые запросы → SearXNG (open-source
метапоиск, сервис в compose) отдаёт ссылки → код скачивает страницы с той
же SSRF-защитой, что и картинки (ADR-026/028: только публичные адреса,
без редиректов, лимит байт) → модель извлекает факты, каждый с дословной
цитатой → код оставляет только факты, чья цитата найдена в тексте
страницы и чьи числа есть в цитате.

Текст страницы — данные, не инструкции: в промпт он идёт отдельным полем
с явным запретом выполнять его указания, а наружу выходит только факт,
подтверждённый цитатой. Любой сбой (поиск, сеть, модель, бюджет времени)
даёт меньше фактов, а не падение генерации.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlparse

import httpx
from lxml import html as lxml_html
from pydantic import BaseModel, Field

from deckdna.contracts.content_pack import (
    Block,
    ContentPack,
    ExternalSource,
    Kind,
    Origin,
    SourceRef,
)
from deckdna.contracts.content_pack import Section as PackSection
from deckdna.contracts.dataset import Dataset
from deckdna.contracts.deck_plan import Brief
from deckdna.providers.base import ModelGateway
from deckdna.research.citations import (
    clean_boilerplate_lines,
    format_source_footnote,
    resolve_publisher_name,
    select_page_excerpt,
)
from deckdna.research.config import WebResearchConfig
from deckdna.research.gaps import (
    analyze_brief_gaps,
    extract_research_subject,
    is_meta_presentation_query,
)
from deckdna.research.numbers_chart import (
    GroundedNumber,
    datasets_from_numbers,
    extract_grounded_numbers,
)

logger = logging.getLogger(__name__)

QUERIES_PROMPT = "research_queries"
EXTRACT_PROMPT = "research_extract"

_MIN_QUOTE_CHARS = 10
_MAX_QUOTE_CHARS = 400
_MAX_FACT_CHARS = 240
_MIN_SNIPPET_FALLBACK_CHARS = 40
_MAX_CONCURRENT_FETCHES = 4
_MAX_CONCURRENT_EXTRACTS = 4
_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "svg",
    "iframe",
    "button",
)
_TEXT_TAGS = ("h1", "h2", "h3", "h4", "p", "li", "blockquote", "td", "dd")
_NUMBER_RE = re.compile(r"\d[\d\s ]*(?:[.,]\d+)?")
# типографские варианты, которые модель «нормализует» при копировании
_QUOTE_TRANSLATION = str.maketrans(
    {
        "«": '"',
        "»": '"',
        "“": '"',
        "”": '"',
        "„": '"',
        "’": "'",
        "‘": "'",
        "—": "-",
        "–": "-",
        " ": " ",
        " ": " ",
    }
)


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str = ""


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int, language: str) -> list[SearchHit]: ...


class SearxngSearch:
    """SearXNG JSON API (``/search?format=json``). Адрес — доверенная
    конфигурация развёртывания (внутренняя сеть compose), поэтому SSRF-
    проверка к нему не применяется — только к страницам из выдачи."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._transport = transport

    async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
        async with httpx.AsyncClient(
            timeout=self.timeout_s,
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            resp = await client.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "language": language},
            )
            resp.raise_for_status()
            data = resp.json()
        hits: list[SearchHit] = []
        for item in data.get("results", []) if isinstance(data, dict) else []:
            url = item.get("url") if isinstance(item, dict) else None
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=str(item.get("title") or "").strip(),
                    snippet=str(item.get("content") or "").strip(),
                )
            )
            if len(hits) >= limit:
                break
        return hits


class QueryPlan(BaseModel):
    queries: list[str] = Field(min_length=1)


class ExtractedFact(BaseModel):
    text: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    topic: str = Field(min_length=1)


class ExtractResult(BaseModel):
    facts: list[ExtractedFact]


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    published_at: str | None = None
    evidence_kind: str = "page"


@dataclass
class WebResearch:
    sections: list[PackSection] = field(default_factory=list)
    sources: list[ExternalSource] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    pages_fetched: int = 0
    facts_kept: int = 0
    facts_rejected: int = 0
    degraded: str | None = None
    footnote: str = ""
    numeric_facts: list[GroundedNumber] = field(default_factory=list)
    datasets: list[Dataset] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "numeric_facts": [asdict(f) for f in self.numeric_facts],
            "datasets": [d.model_dump(mode="json") for d in self.datasets],
            "questions": list(self.questions) if not self.facts_kept else [],
            "queries": list(self.queries),
            "sources": [
                {
                    "id": s.id,
                    "url": s.url,
                    "title": s.title,
                    "publisher": s.publisher,
                    "retrieved_at": s.retrieved_at,
                    "query": s.query,
                }
                for s in self.sources
            ],
            "pages_fetched": self.pages_fetched,
            "facts_kept": self.facts_kept,
            "added_facts": self.facts_kept,
            "status": (
                "added"
                if self.facts_kept
                else "skipped"
                if self.degraded in ("web_disabled", "no_model")
                else "no_new_facts"
            ),
            "reason": self.degraded or (None if self.facts_kept else "no_new_facts"),
            "facts_rejected": self.facts_rejected,
            "degraded": self.degraded,
            "footnote": self.footnote,
        }


def web_source_id(url: str) -> str:
    """Стабильный id веб-источника по URL (ADR-034 §0): один и тот же
    адрес в разных построениях плана — один id, поэтому фронт может
    исключить источник повторным ``POST /plans`` с ``exclude_source_ids``."""
    key = url.split("#", 1)[0].rstrip("/")
    return "web:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]  # noqa: S324


def source_card(source: ExternalSource, facts_count: int | None = None) -> dict:
    """Источник в форме фронта (ADR-034 §0): ``{id, title, url, site,
    accessed_at, facts_count}``."""
    return {
        "id": source.id,
        "title": source.title,
        "url": source.url,
        "site": source.publisher,
        "accessed_at": source.retrieved_at,
        "facts_count": facts_count,
    }


def can_research(gateway: object) -> bool:
    """Нужна настоящая модель (или mock с fixture'ами обоих промптов)."""
    if gateway is None:
        return False
    if getattr(gateway, "provider_name", None) == "mock":
        fixtures = getattr(gateway, "fixtures", None) or {}
        return QUERIES_PROMPT in fixtures and EXTRACT_PROMPT in fixtures
    return True


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_QUOTE_TRANSLATION)).strip().casefold()


def _numbers(text: str) -> set[str]:
    return {
        re.sub(r"[\s ]", "", m.group()).replace(",", ".").rstrip(".")
        for m in _NUMBER_RE.finditer(text)
    }


def verify_fact(
    fact: ExtractedFact, page_text: str, *, max_quote_chars: int = _MAX_QUOTE_CHARS
) -> bool:
    """Факт принимается, только если цитата дословно есть на странице, а
    каждое число факта есть в цитате (модель не «улучшила» цифры)."""
    quote = _normalize(fact.quote)
    if not (_MIN_QUOTE_CHARS <= len(quote) <= max_quote_chars):
        return False
    if len(fact.text.strip()) > _MAX_FACT_CHARS:
        return False
    if quote not in _normalize(page_text):
        return False
    return _numbers(fact.text) <= _numbers(fact.quote)


def html_to_text(raw: str) -> tuple[str, str]:
    """(title, текст) страницы: только содержательные блоки, без меню,
    скриптов, cookie-баннеров и подвалов."""
    try:
        doc = lxml_html.fromstring(raw)
    except (ValueError, lxml_html.etree.ParserError):
        return "", ""
    title_el = doc.find(".//title")
    title = re.sub(r"\s+", " ", title_el.text_content()).strip() if title_el is not None else ""
    for el in doc.xpath(
        "|".join(f"//{tag}" for tag in _DROP_TAGS)
        + '|//sup[contains(@class, "reference") or contains(@class, "mw-ref")]'
    ):
        el.drop_tree()
    raw_lines: list[str] = []
    short_li_buf: list[str] = []
    for el in doc.iter("tr", *_TEXT_TAGS):
        if el.tag != "li" and short_li_buf:
            joined = " ".join(short_li_buf)
            if len(joined) >= 25:
                raw_lines.append(joined)
            short_li_buf.clear()
        if el.tag == "tr":
            if not (el.xpath("./th") and el.xpath("./td")):
                continue
            cells = [
                re.sub(r"\s+", " ", c.text_content()).strip()
                for c in el.xpath("./th|./td")
                if c.text_content().strip()
            ]
            if 2 <= len(cells) <= 4:
                row_text = " — ".join(cells)
                if 25 <= len(row_text) <= 300:
                    raw_lines.append(row_text)
            continue
        text = re.sub(r"\s+", " ", el.text_content()).strip()
        if el.tag == "li" and 10 <= len(text) < 25:
            short_li_buf.append(text)
            continue
        if len(text) < 25:
            continue
        raw_lines.append(text)
    if short_li_buf:
        joined = " ".join(short_li_buf)
        if len(joined) >= 25:
            raw_lines.append(joined)
    lines = clean_boilerplate_lines(raw_lines)
    if not lines:
        body = doc.find(".//body")
        text = re.sub(r"\s+", " ", (body if body is not None else doc).text_content()).strip()
        return title, text
    return title, "\n".join(lines)


def _default_http_proxy() -> str | None:
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("http_proxy")
    )


async def _host_is_public(host: str) -> bool:
    # та же проверка, что у удалённых картинок (ADR-028)
    from deckdna.pptx.composing.image_fill import _allowed_remote_host

    return await asyncio.to_thread(_allowed_remote_host, host)


async def fetch_page(
    url: str, cfg: WebResearchConfig, transport: httpx.AsyncBaseTransport | None = None
) -> Page | None:
    """HTML страницы → Page; None при любой проблеме (не публичный адрес,
    редирект, не HTML, превышен размер, сетевая ошибка)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if not await _host_is_public(parsed.hostname):
        return None
    proxy = None if transport is not None else _default_http_proxy()
    try:
        async with (
            httpx.AsyncClient(
                timeout=cfg.page_timeout_s,
                transport=transport,
                follow_redirects=False,
                proxy=proxy,
                trust_env=False,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 "
                        "DeckDNA-research/1.0 (+presentation content fill)"
                    )
                },
            ) as client,
            client.stream("GET", url) as resp,
        ):
            if resp.status_code != 200:
                return None
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype and "text/plain" not in ctype:
                return None
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > cfg.max_page_bytes:
                return None
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > cfg.max_page_bytes:
                    return None
                chunks.append(chunk)
            raw = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
    except (httpx.HTTPError, OSError, ValueError):
        return None
    if "html" in ctype:
        title, text = html_to_text(raw)
    else:
        title, text = "", raw
    if len(text) < 200:
        return None
    published_at = None
    if "html" in ctype:
        try:
            doc = lxml_html.fromstring(raw)
            dates = doc.xpath(
                '//meta[@property="article:published_time"]/@content | '
                '//meta[@name="date"]/@content | //time/@datetime'
            )
            published_at = next((d[:10] for d in dates if re.match(r"20\d\d-\d\d-\d\d", d)), None)
        except (ValueError, lxml_html.etree.ParserError):
            pass
    return Page(url=url, title=title or parsed.hostname, text=text, published_at=published_at)


def _brief_payload(brief: Brief) -> dict:
    return {
        "purpose": brief.purpose,
        "audience": brief.audience,
        "language": brief.language,
        "tone": brief.tone,
    }


async def plan_queries(
    brief: Brief,
    gateway: ModelGateway,
    *,
    covered_topics: list[str],
    missing_slides: int,
    max_queries: int,
    focus: str = "facts",
    pack: ContentPack | None = None,
) -> list[str]:
    gaps = analyze_brief_gaps(pack, brief, max_queries=max_queries, focus=focus)
    payload = {
        "brief": _brief_payload(brief),
        "covered_topics": covered_topics,
        "missing_slides": missing_slides,
        "max_queries": max_queries,
        "focus": focus,
        "missing_dimensions": gaps.to_prompt_list(brief.language or "ru"),
        "subject": asdict(extract_research_subject(brief.purpose)),
        "targeted_queries": gaps.targeted_queries,
    }
    queries: list[str] = []
    try:
        if not can_research(gateway):
            return list(gaps.targeted_queries)[:max_queries]
        result = await gateway.text_json(QUERIES_PROMPT, payload, QueryPlan)
        if isinstance(result, QueryPlan):
            queries = result.queries
    except Exception as exc:  # noqa: BLE001 — без модели ищем по выявленным пробелам брифа
        logger.warning("research_queries failed (%s); using gap-targeted queries", exc)
    seen: set[str] = set()
    cleaned: list[str] = []
    for query in queries:
        q = re.sub(r"\s+", " ", query).strip().strip('"')
        if 2 <= len(q) <= 120 and not is_meta_presentation_query(q) and q.casefold() not in seen:
            seen.add(q.casefold())
            cleaned.append(q)
    subject = extract_research_subject(brief.purpose)
    if subject.kind == "entity":
        # Детерминированный бюджет сначала закрывает показатели объекта.
        cleaned = list(
            dict.fromkeys([*gaps.targeted_queries, *(q for q in cleaned if subject.matches(q))])
        )
    if not cleaned:
        cleaned = list(gaps.targeted_queries)
    return cleaned[:max_queries]


async def _extract(
    page: Page, brief: Brief, gateway: ModelGateway, cfg: WebResearchConfig, focus: str = "facts"
) -> list[ExtractedFact]:
    payload = {
        "brief": _brief_payload(brief),
        "page": {
            "title": page.title,
            "text": select_page_excerpt(page.text, cfg.max_page_chars),
        },
        "max_facts": cfg.max_facts_per_page,
        "focus": focus,
    }
    try:
        result = await gateway.text_json(EXTRACT_PROMPT, payload, ExtractResult)
    except Exception as exc:  # noqa: BLE001 — страница без фактов, не падение
        logger.warning("research_extract failed for %s (%s)", page.url, exc)
        return []
    return result.facts[: cfg.max_facts_per_page] if isinstance(result, ExtractResult) else []


async def _gather_within(coros: list[Awaitable], deadline: float) -> list:
    """Результаты задач, успевших до дедлайна; остальные отменяются."""
    if not coros:
        return []
    tasks = [asyncio.ensure_future(c) for c in coros]
    timeout = max(deadline - time.monotonic(), 0.0)
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        # дождаться отмены, чтобы не оставлять висящие задачи в цикле
        await asyncio.gather(*pending, return_exceptions=True)
    results = []
    for task in tasks:
        if task in done and not task.cancelled() and task.exception() is None:
            results.append(task.result())
        else:
            results.append(None)
    return results


async def research_web(
    pack: ContentPack,
    brief: Brief,
    gateway: ModelGateway,
    cfg: WebResearchConfig,
    *,
    missing_slides: int,
    covered_topics: list[str],
    deadline: float,
    search: SearchProvider | None = None,
    fetch: Callable[[str], Awaitable[Page | None]] | None = None,
    on_event: Callable[[str, dict], None] | None = None,
    focus: str = "facts",
    min_facts_per_topic: int = 2,
    exclude_source_ids: set[str] | frozenset[str] = frozenset(),
) -> WebResearch:
    """Факты из интернета, подтверждённые цитатами, как разделы пака.

    ``on_event`` получает прогресс для живого экрана плана:
    ``research.queries`` и ``research.source`` (страница, давшая факты)."""
    emit = on_event or (lambda _type, _data: None)
    deadline = min(deadline, time.monotonic() + 30.0)
    subject = extract_research_subject(brief.purpose)
    report = WebResearch(
        questions=[
            f"Какой период и какие показатели нужны по теме «{subject.name}»?",
            "Пришлите отчётность или ссылки на первоисточники с нужными показателями.",
            "Уточните точное название организации/продукта и географию анализа.",
        ]
    )
    if search is None:
        if not cfg.available:
            report.degraded = "web_disabled"
            return report
        from deckdna.research.providers import build_search_provider

        search = build_search_provider(
            cfg.search_url, timeout_s=cfg.page_timeout_s + 3, searxng_cls=SearxngSearch
        )

    async def _default_fetch(url: str) -> Page | None:
        return await fetch_page(url, cfg)

    fetcher = fetch or _default_fetch

    language = (brief.language or pack.language or "ru")[:2]
    query_task = plan_queries(
        brief,
        gateway,
        covered_topics=covered_topics,
        missing_slides=missing_slides,
        max_queries=cfg.max_queries,
        focus=focus,
        pack=pack,
    )
    query_results = await _gather_within([query_task], min(deadline, time.monotonic() + 2.0))
    report.queries = (
        query_results[0]
        or analyze_brief_gaps(
            pack, brief, max_queries=cfg.max_queries, focus=focus
        ).targeted_queries
    )
    if time.monotonic() >= deadline:
        report.degraded = "budget_exhausted"
        return report
    emit("research.queries", {"queries": list(report.queries)})
    # ADR-034 §0: форма фронта — по событию на запрос
    for query in report.queries:
        emit("research.query", {"query": query})

    async def _search(query: str) -> list[SearchHit]:
        try:
            return await search.search(query, cfg.results_per_query, language)
        except Exception as exc:  # noqa: BLE001 — один запрос не валит ресёрч
            logger.warning("search failed for %r (%s)", query, exc)
            return []

    hit_lists = await _gather_within([_search(q) for q in report.queries], deadline)
    picked: list[tuple[SearchHit, str]] = []
    seen_urls: set[str] = set()
    # Round-robin: один широкий запрос не занимает все страницы.
    for rank in range(cfg.results_per_query):
        for query, hits in zip(report.queries, hit_lists, strict=True):
            if not hits or rank >= len(hits):
                continue
            hit = hits[rank]
            key = hit.url.split("#", 1)[0].rstrip("/")
            if key in seen_urls or (
                exclude_source_ids
                and (web_source_id(hit.url) in exclude_source_ids or hit.url in exclude_source_ids)
            ):
                continue
            seen_urls.add(key)
            picked.append((hit, query))
    picked = picked[: cfg.max_pages]
    if not picked:
        report.degraded = "no_search_results"
        return report

    fetch_sem = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

    async def _fetch(url: str) -> Page | None:
        async with fetch_sem:
            return await fetcher(url)

    pages = await _gather_within([_fetch(hit.url) for hit, _ in picked], deadline)
    fetched: list[tuple[Page, SearchHit, str]] = []
    for page, (hit, query) in zip(pages, picked, strict=True):
        snippet = re.sub(r"\s+", " ", (hit.snippet or "")).strip()
        if page is not None:
            fetched.append((page, hit, query))
        elif (
            subject.kind != "entity"
            and can_research(gateway)
            and len(snippet) >= _MIN_SNIPPET_FALLBACK_CHARS
        ):
            # Legacy qualitative fallback stays explicitly separate from downloaded evidence.
            fetched.append(
                (
                    Page(
                        url=hit.url, title=hit.title, text=snippet, evidence_kind="search_snippet"
                    ),
                    hit,
                    query,
                )
            )
    report.pages_fetched = sum(1 for p, _, _ in fetched if p.evidence_kind == "page")
    if not fetched:
        report.degraded = "no_pages"
        return report

    extract_sem = asyncio.Semaphore(_MAX_CONCURRENT_EXTRACTS)

    async def _extract_limited(page: Page) -> list[ExtractedFact]:
        if not can_research(gateway):
            return []
        async with extract_sem:
            return await _extract(page, brief, gateway, cfg, focus)

    # Сначала извлекаем проверяемые цифры: модель не может израсходовать
    # остаток бюджета до сохранения детерминированного результата.
    page_numbers = [
        extract_grounded_numbers(
            p.text, subject, p.url, resolve_publisher_name(p.url, p.title), p.published_at
        )[: max(cfg.max_facts_per_page * 3, 18)]
        if p.evidence_kind == "page"
        else []
        for p, _, _ in fetched
    ]
    fact_lists = await _gather_within([_extract_limited(p) for p, _, _ in fetched], deadline)

    retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")
    known = {
        _normalize(t)
        for s in pack.sections
        for b in s.blocks
        for t in [b.text, *(b.items or [])]
        if t
    }
    by_topic: dict[str, list[tuple[str, str]]] = {}
    topic_titles: dict[str, str] = {}
    number_keys: set[tuple] = set()
    for (page, hit, query), facts, numbers in zip(fetched, fact_lists, page_numbers, strict=True):
        source_id = web_source_id(page.url)
        kept_here = 0
        deterministic: list[ExtractedFact] = []
        for number in numbers:
            candidate = ExtractedFact(
                text=number.text, quote=number.quote, topic=f"{subject.name}: показатели"
            )
            if not verify_fact(candidate, page.text, max_quote_chars=800):
                report.facts_rejected += 1
                continue
            key = (number.entity, number.metric, number.value, number.unit, number.period)
            if key in number_keys:
                continue
            number_keys.add(key)
            report.numeric_facts.append(number)
            if (
                subject.kind != "entity"
                and facts
                and any(
                    verify_fact(f, page.text) and _numbers(number.text) <= _numbers(f.text)
                    for f in facts
                )
            ):
                continue  # сохраняем привычную формулировку модели без дубля
            deterministic.append(candidate)
        for fact in [*deterministic, *(facts or [])]:
            text = re.sub(r"\s+", " ", fact.text).strip()
            if (
                not verify_fact(
                    fact, page.text, max_quote_chars=800 if fact in deterministic else 400
                )
                or (page.evidence_kind != "page" and _numbers(text))
                or (
                    subject.kind == "entity"
                    and fact not in deterministic
                    and not subject.matches(fact.quote)
                )
                or (subject.kind == "entity" and _numbers(text) and fact not in deterministic)
                or _normalize(text) in known
                or (focus == "numbers" and not _numbers(text))
            ):
                report.facts_rejected += 1
                continue
            known.add(_normalize(text))
            topic = re.sub(r"\s+", " ", fact.topic).strip().rstrip(".")[:80]
            key = topic.casefold()
            topic_titles.setdefault(key, topic)
            by_topic.setdefault(key, []).append((text, source_id))
            kept_here += 1
        report.facts_kept += kept_here
        if kept_here:
            raw_title = (page.title or hit.title or urlparse(page.url).hostname or "")[:200]
            report.sources.append(
                ExternalSource(
                    id=source_id,
                    origin=Origin.web,
                    url=page.url,
                    title=raw_title,
                    publisher=resolve_publisher_name(page.url, raw_title),
                    retrieved_at=retrieved_at,
                    query=query,
                )
            )

    report.datasets = datasets_from_numbers(report.numeric_facts)

    for i, (key, facts) in enumerate(by_topic.items()):
        blocks: list[Block] = []
        for source_id in dict.fromkeys(src for _, src in facts):
            blocks.append(
                Block(
                    kind=Kind.list,
                    items=[text for text, src in facts if src == source_id],
                    source_ref=SourceRef(artifact_id=source_id),
                )
            )
        report.sections.append(
            PackSection(
                id=f"sec-web-{i}",
                heading=topic_titles[key],
                level=2,
                blocks=blocks,
            )
        )
    used = {b.source_ref.artifact_id for s in report.sections for b in s.blocks}
    report.sources = [s for s in report.sources if s.id in used]
    report.footnote = format_source_footnote(report.sources, language=language)
    for source in report.sources:
        facts_count = sum(
            len(b.items or [])
            for s in report.sections
            for b in s.blocks
            if b.source_ref.artifact_id == source.id
        )
        emit(
            "research.source",
            {
                "url": source.url,
                "title": source.title,
                "publisher": source.publisher,
                "facts": facts_count,
                # ADR-034 §0: форма фронта
                "source": source_card(source, facts_count),
            },
        )
    if not report.sections:
        report.degraded = (
            "budget_exhausted" if time.monotonic() >= deadline else "no_verified_facts"
        )
    logger.info(
        "web research: %d queries, %d pages, %d facts kept, %d rejected",
        len(report.queries),
        report.pages_fetched,
        report.facts_kept,
        report.facts_rejected,
    )
    return report
