"""Провайдеры быстрого внешнего веб-поиска (Web Grounding).

Реализует единый протокол ``SearchProvider`` для четырёх классов источников:
1. ``DuckDuckGoSearch`` — полностью бесплатный бесключевой поиск через
   легковесную HTML/Lite-выдачу DuckDuckGo (``httpx`` + ``lxml``, без
   сторонних пакетов, задержка ~0.6–1.4 с).
2. ``TavilySearch`` — структурированный RAG-поиск (JSON API с очищенными
   сниппетами высокой плотности фактов, задержка ~0.5–1.2 с).
3. ``SerperSearch`` — быстрый Google SERP API (``answerBox`` + ``organic``
   сниппеты, задержка ~0.4–0.9 с).
4. ``FallbackSearchProvider`` — каскадный оркестратор с автоматическим
   переключением при недоступности или пустой выдаче основного провайдера.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
from lxml import html as lxml_html

from deckdna.research.web import SearchHit, SearchProvider, SearxngSearch

logger = logging.getLogger(__name__)

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_BING_NEWS_RSS_URL = "https://www.bing.com/news/search"
_BING_WEB_RSS_URL = "https://www.bing.com/search"
_TAVILY_API_URL = "https://api.tavily.com/search"
_SERPER_API_URL = "https://google.serper.dev/search"
_QUERY_STOPWORDS_RE = re.compile(
    r"\b(?:компания|количество|отчетность|аналитика|статистика|метрики|динамика|"
    r"лидеры|конкуренты|рентабельность|ltv|cac|рф|по\s+годам)\b",
    re.I,
)
_SKIP_WEB_HOSTS = ("2gis.", "play.google.com", "apps.apple.com", "youtube.com", "vk.com")


def unwrap_ddg_url(raw_href: str) -> str | None:
    """Извлекает прямой целевой URL из редирект-ссылки DuckDuckGo ``/l/?uddg=...``."""
    href = (raw_href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.hostname or "") and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [None])[0]
        if uddg:
            href = unquote(uddg).strip()
            parsed = urlparse(href)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    # Исключаем рекламные трекеры DuckDuckGo
    if "duckduckgo.com" in (parsed.hostname or "") and "y.js" in parsed.path:
        return None
    return href


def _unwrap_bing_url(raw_link: str) -> str | None:
    link = (raw_link or "").strip()
    if not link:
        return None
    parsed = urlparse(link)
    if "bing.com" in (parsed.hostname or "") and "apiclick.aspx" in parsed.path:
        qs = parse_qs(parsed.query)
        target = qs.get("url", [None])[0]
        if target:
            link = unquote(target).strip()
            parsed = urlparse(link)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return link


def _simplify_news_query(query: str) -> str:
    q = _QUERY_STOPWORDS_RE.sub(" ", query)
    q = re.sub(r"\bмагазинов\b", "магазины", q, flags=re.I)
    q = re.sub(r"\b(20\d\d)\s+20\d\d\b", r"\1", q)
    words = q.split()
    return " ".join(words[:5]) if words else query


def _parse_bing_rss(raw_xml: str, *, skip_homepages: bool = False) -> list[SearchHit]:
    try:
        root = ET.fromstring(raw_xml)  # noqa: S314
    except ET.ParseError:
        return []
    hits: list[SearchHit] = []
    for item in root.findall(".//item"):
        url = _unwrap_bing_url(item.findtext("link") or "")
        if not url:
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if any(skip in host for skip in _SKIP_WEB_HOSTS):
            continue
        if skip_homepages and parsed.path in ("", "/"):
            continue
        title = re.sub(r"\s+", " ", (item.findtext("title") or "")).strip()
        desc = re.sub(r"<[^>]+>", " ", (item.findtext("description") or ""))
        snippet = re.sub(r"\s+", " ", desc).strip()
        hits.append(SearchHit(url=url, title=title, snippet=snippet))
    return hits


def default_http_proxy() -> str | None:
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("http_proxy")
    )


class DuckDuckGoSearch:
    """Бесплатный бесключевой поиск по DuckDuckGo HTML endpoint."""

    def __init__(
        self,
        endpoint: str = _DDG_HTML_URL,
        timeout_s: float = 1.8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self._transport = transport

    async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
        kl = "ru-ru" if language.startswith("ru") else "wt-wt"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        proxy = None if self._transport is not None else default_http_proxy()
        client_timeout = httpx.Timeout(self.timeout_s, connect=min(self.timeout_s, 0.8))
        async with httpx.AsyncClient(
            timeout=client_timeout,
            transport=self._transport,
            follow_redirects=True,
            headers=headers,
            proxy=proxy,
            trust_env=False,
        ) as client:
            hits: list[SearchHit] = []
            try:
                resp = await client.get(self.endpoint, params={"q": query, "kl": kl})
                if resp.status_code not in (202, 403, 429):
                    resp.raise_for_status()
                raw_html = resp.text
                hits = parse_ddg_html(raw_html, limit=limit)
                if not hits and self.endpoint == _DDG_HTML_URL:
                    resp = await client.get(
                        "https://lite.duckduckgo.com/lite/", params={"q": query}
                    )
                    if resp.status_code not in (202, 403, 429):
                        resp.raise_for_status()
                    hits = parse_ddg_html(resp.text, limit=limit)
            except (TimeoutError, httpx.HTTPError):
                hits = []
            if hits:
                return hits
            if self._transport is None:
                return await _search_public_fallback(client, query, limit, language)

        return hits


async def _search_public_fallback(
    client: httpx.AsyncClient, query: str, limit: int, language: str
) -> list[SearchHit]:
    """Бесключевой резерв (Bing RSS + Wikipedia API), когда DDG отдаёт 202 CAPTCHA."""
    news_q = _simplify_news_query(query)
    wiki_lang = "ru" if language.startswith("ru") else "en"
    wiki_q = " ".join(news_q.split()[:2]) or query

    async def _bing_news() -> list[SearchHit]:
        try:
            r = await client.get(
                _BING_NEWS_RSS_URL,
                params={"q": news_q, "format": "rss"},
                headers={"User-Agent": "curl/8.5.0"},
            )
            return _parse_bing_rss(r.text) if r.status_code == 200 else []
        except httpx.HTTPError:
            return []

    async def _bing_web() -> list[SearchHit]:
        try:
            r = await client.get(
                _BING_WEB_RSS_URL,
                params={"q": query, "format": "rss"},
                headers={"User-Agent": "curl/8.5.0"},
            )
            return _parse_bing_rss(r.text, skip_homepages=True) if r.status_code == 200 else []
        except httpx.HTTPError:
            return []

    async def _wiki() -> list[SearchHit]:
        try:
            r = await client.get(
                f"https://{wiki_lang}.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": wiki_q,
                    "format": "json",
                    "srlimit": 2,
                },
            )
            if r.status_code != 200:
                return []
            items = r.json().get("query", {}).get("search", [])
            out: list[SearchHit] = []
            for item in items:
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                url = f"https://{wiki_lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                snippet = re.sub(r"<[^>]+>", " ", str(item.get("snippet") or ""))
                out.append(
                    SearchHit(
                        url=url,
                        title=f"{title} — Википедия",
                        snippet=re.sub(r"\s+", " ", snippet).strip(),
                    )
                )
            return out
        except (httpx.HTTPError, ValueError):
            return []

    news_hits, web_hits, wiki_hits = await asyncio.gather(_bing_news(), _bing_web(), _wiki())
    combined: list[SearchHit] = []
    seen: set[str] = set()
    # Включаем профильную статью Википедии первой для карточек и таблиц динамики,
    # затем свежие новостные и веб-источники.
    for source_list in (wiki_hits[:1], news_hits, web_hits, wiki_hits[1:]):
        for hit in source_list:
            key = hit.url.split("#", 1)[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            combined.append(hit)
            if len(combined) >= limit:
                return combined
    return combined


def parse_ddg_html(raw_html: str, limit: int = 5) -> list[SearchHit]:
    """Парсит HTML-выдачу DuckDuckGo в список ``SearchHit``."""
    try:
        doc = lxml_html.fromstring(raw_html)
    except (ValueError, lxml_html.etree.ParserError):
        return []

    hits: list[SearchHit] = []
    seen_urls: set[str] = set()
    xpath_expr = '//div[contains(@class, "result") and not(contains(@class, "result--ad"))]'
    for node in doc.xpath(xpath_expr):
        anchors = node.xpath('.//a[contains(@class, "result__a")]')
        if not anchors:
            continue
        href = unwrap_ddg_url(anchors[0].get("href", ""))
        if not href or href in seen_urls:
            continue
        title = re.sub(r"\s+", " ", anchors[0].text_content()).strip()
        snippet_nodes = node.xpath('.//*[contains(@class, "result__snippet")]')
        snippet = (
            re.sub(r"\s+", " ", snippet_nodes[0].text_content()).strip() if snippet_nodes else ""
        )
        seen_urls.add(href)
        hits.append(SearchHit(url=href, title=title, snippet=snippet))
        if len(hits) >= limit:
            break
    if not hits:
        for anchor in doc.xpath('//a[contains(@class, "result-link")]'):
            href = unwrap_ddg_url(anchor.get("href", ""))
            if not href or href in seen_urls:
                continue
            row = anchor.xpath("ancestor::tr[1]")
            snippets = (
                row[0].xpath('following-sibling::tr[1]//td[contains(@class, "result-snippet")]')
                if row
                else []
            )
            snippet = snippets[0].text_content() if snippets else ""
            seen_urls.add(href)
            hits.append(
                SearchHit(
                    url=href,
                    title=anchor.text_content().strip(),
                    snippet=re.sub(r"\s+", " ", snippet).strip(),
                )
            )
            if len(hits) >= limit:
                break
    return hits


class TavilySearch:
    """Структурированный RAG-поиск через Tavily Search API."""

    def __init__(
        self,
        api_key: str,
        api_url: str = _TAVILY_API_URL,
        search_depth: str = "basic",
        timeout_s: float = 1.8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_url = api_url
        self.search_depth = search_depth
        self.timeout_s = timeout_s
        self._transport = transport

    async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": self.search_depth,
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_s, transport=self._transport, follow_redirects=False
        ) as client:
            resp = await client.post(self.api_url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        hits: list[SearchHit] = []
        for item in data.get("results", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=str(item.get("title") or "").strip(),
                    snippet=re.sub(r"\s+", " ", str(item.get("content") or "")).strip(),
                )
            )
            if len(hits) >= limit:
                break
        return hits


class SerperSearch:
    """Быстрый структурированный поиск через Serper (Google Search API)."""

    def __init__(
        self,
        api_key: str,
        api_url: str = _SERPER_API_URL,
        timeout_s: float = 1.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_url = api_url
        self.timeout_s = timeout_s
        self._transport = transport

    async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
        lang = (language or "ru")[:2].casefold()
        gl = "ru" if lang == "ru" else "us"
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        body = {"q": query, "num": limit, "gl": gl, "hl": lang}
        async with httpx.AsyncClient(
            timeout=self.timeout_s, transport=self._transport, follow_redirects=False
        ) as client:
            resp = await client.post(self.api_url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, dict):
            return []
        hits: list[SearchHit] = []
        answer_box = data.get("answerBox")
        if isinstance(answer_box, dict):
            ab_url = answer_box.get("link")
            ab_snippet = answer_box.get("snippet") or answer_box.get("answer") or ""
            is_valid_ab = (
                isinstance(ab_url, str)
                and ab_url.startswith(("http://", "https://"))
                and bool(ab_snippet)
            )
            if is_valid_ab:
                hits.append(
                    SearchHit(
                        url=ab_url,
                        title=str(answer_box.get("title") or "").strip(),
                        snippet=re.sub(r"\s+", " ", str(ab_snippet)).strip(),
                    )
                )
        for item in data.get("organic", []):
            if not isinstance(item, dict):
                continue
            url = item.get("link")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=str(item.get("title") or "").strip(),
                    snippet=re.sub(r"\s+", " ", str(item.get("snippet") or "")).strip(),
                )
            )
            if len(hits) >= limit:
                break
        return hits


class FallbackSearchProvider:
    """Каскадный провайдер поиска: при ошибке или пустом ответе переходит к резервному."""

    def __init__(self, providers: list[SearchProvider]) -> None:
        self.providers = list(providers)

    async def search(self, query: str, limit: int, language: str) -> list[SearchHit]:
        for provider in self.providers:
            try:
                hits = await provider.search(query, limit, language)
                if hits:
                    return hits
            except Exception as exc:  # noqa: BLE001 — переключение на резервный провайдер
                logger.warning(
                    "search provider %s failed for %r (%s); trying fallback",
                    type(provider).__name__,
                    query,
                    exc,
                )
        return []


def build_search_provider(
    search_url: str,
    *,
    timeout_s: float = 8.0,
    searxng_cls: type[SearxngSearch] = SearxngSearch,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SearchProvider:
    """Фабрика поискового провайдера по URL-конфигурации ``DECKDNA_RESEARCH_SEARCH_URL``.

    Поддерживаемые схемы:
    - ``http://...`` / ``https://...`` → ``SearxngSearch``
    - ``ddg://`` / ``duckduckgo://`` → ``DuckDuckGoSearch``
    - ``tavily://<api_key>`` → ``TavilySearch``
    - ``serper://<api_key>`` → ``SerperSearch``
    """
    raw = (search_url or "").strip()
    parsed = urlparse(raw)
    scheme = parsed.scheme.casefold()

    if scheme in ("ddg", "duckduckgo"):
        return DuckDuckGoSearch(timeout_s=min(timeout_s, 2.0), transport=transport)
    if scheme == "tavily":
        api_key = parsed.netloc or parsed.path.lstrip("/")
        return TavilySearch(api_key=api_key, timeout_s=min(timeout_s, 2.0), transport=transport)
    if scheme == "serper":
        api_key = parsed.netloc or parsed.path.lstrip("/")
        return SerperSearch(api_key=api_key, timeout_s=min(timeout_s, 1.8), transport=transport)
    return searxng_cls(raw, timeout_s=timeout_s, transport=transport)


__all__ = [
    "DuckDuckGoSearch",
    "FallbackSearchProvider",
    "SerperSearch",
    "TavilySearch",
    "build_search_provider",
    "parse_ddg_html",
    "unwrap_ddg_url",
]
