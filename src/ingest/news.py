from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from typing import Callable, TypeVar
from urllib.parse import quote_plus
import re
import time

import feedparser
import httpx

from src import USER_AGENT
from src.models import NewsItem
from src.settings import load_yaml
from src.timeutil import isoformat, parse_datetime

T = TypeVar("T")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
RETRY_STATUSES = {429, 500, 502, 503, 504}
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]{3,}")


def http_client(timeout: float | None = None, *, browser: bool = False) -> httpx.Client:
    budgets = load_yaml("budgets.yml")
    seconds = timeout if timeout is not None else float(budgets.get("http_timeout_seconds") or 12)
    headers = {
        "User-Agent": BROWSER_UA if browser else USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    return httpx.Client(
        headers=headers,
        timeout=httpx.Timeout(seconds, connect=8.0),
        follow_redirects=True,
    )


def classify_source(source: str, url: str = "", title: str = "") -> tuple[str, float]:
    blob = f"{source} {url} {title}".lower()
    ranks = load_yaml("sources.yml").get("source_ranks") or {}
    for class_name in ("official", "major_media", "blog", "google_news"):
        spec = ranks.get(class_name) or {}
        for token in spec.get("match") or []:
            if str(token).lower() in blob:
                try:
                    return class_name, float(spec.get("weight") or 1.0)
                except (TypeError, ValueError):
                    return class_name, 1.0
    return "other", 1.0


def _annotate(item: NewsItem) -> NewsItem:
    class_name, weight = classify_source(item.source, item.url, item.title)
    item.sourceClass = class_name
    item.sourceWeight = weight
    return item


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", unescape(cleaned)).strip()


def compact_http_error(exc: BaseException) -> str:
    text = str(exc).strip()
    first = text.splitlines()[0] if text else type(exc).__name__
    if "503" in text:
        return "503 限流"
    if "429" in text:
        return "429 限流"
    if "403" in text:
        return "403 拒绝"
    if "418" in text:
        return "418 拒绝"
    if "Name or service not known" in text or "getaddrinfo" in text:
        return "DNS 失败"
    if "ConnectError" in type(exc).__name__ or "ConnectError" in text:
        return "连接失败"
    return first[:180]


def run_parallel(tasks: list[Callable[[], T]], workers: int = 8) -> list[T | Exception]:
    if not tasks:
        return []
    results: list[T | Exception] = [Exception("not started")] * len(tasks)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {pool.submit(task): index for index, task in enumerate(tasks)}
        for future in as_completed(future_map):
            index = future_map[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - adapters must not crash the job
                results[index] = exc
    return results


def _retries() -> int:
    return max(0, int(load_yaml("budgets.yml").get("news_retries") or 2))


def fetch_rss_bytes(url: str, *, retries: int | None = None, browser: bool = True) -> bytes:
    attempts = (retries if retries is not None else _retries()) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with http_client(browser=browser) as client:
                response = client.get(url)
                if response.status_code in RETRY_STATUSES and attempt < attempts - 1:
                    time.sleep(0.7 * (attempt + 1))
                    last_error = httpx.HTTPStatusError(
                        f"{response.status_code} {response.reason_phrase} for url '{url}'",
                        request=response.request,
                        response=response,
                    )
                    continue
                response.raise_for_status()
                return response.content
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.7 * (attempt + 1))
                continue
            raise
    raise last_error or RuntimeError(f"failed to fetch {url}")


def _items_from_feed(content: bytes, source: str, limit: int) -> list[NewsItem]:
    parsed = feedparser.parse(content)
    items: list[NewsItem] = []
    for entry in parsed.entries[:limit]:
        published = (
            getattr(entry, "published", "")
            or getattr(entry, "updated", "")
            or ""
        )
        dt = parse_datetime(str(published))
        summary = strip_html(str(getattr(entry, "summary", "") or getattr(entry, "description", "") or ""))
        items.append(
            _annotate(
                NewsItem(
                    title=strip_html(str(getattr(entry, "title", "") or "")),
                    source=source,
                    url=str(getattr(entry, "link", "") or ""),
                    publishedAt=isoformat(dt),
                    snippet=summary[:400],
                )
            )
        )
    return [item for item in items if item.title]


def fetch_rss(url: str, source: str, limit: int, *, browser: bool = True) -> list[NewsItem]:
    return _items_from_feed(fetch_rss_bytes(url, browser=browser), source, limit)


def fetch_configured_rss(limit: int) -> tuple[list[NewsItem], list[str]]:
    config = load_yaml("sources.yml")
    budgets = load_yaml("budgets.yml")
    workers = int(budgets.get("concurrent_fetches") or 8)
    feeds = config.get("rss") or []

    def make(feed: dict) -> Callable[[], list[NewsItem]]:
        return lambda: fetch_rss(feed["url"], feed.get("name") or feed["id"], limit)

    raw = run_parallel([make(feed) for feed in feeds], workers=workers)
    items: list[NewsItem] = []
    errors: list[str] = []
    for feed, result in zip(feeds, raw, strict=True):
        if isinstance(result, Exception):
            errors.append(f"RSS {feed.get('id')}: {compact_http_error(result)}")
        else:
            items.extend(result)
    return items, errors


def google_editions_for_query(query: str, templates: dict) -> list[str]:
    """Skip the English edition for Chinese tape queries; skip zh when the query is English-only."""
    cjk = len(_CJK.findall(query or ""))
    latin = len(_LATIN.findall(query or ""))
    langs: list[str] = []
    if "zh" in templates and (cjk > 0 or latin == 0):
        langs.append("zh")
    if "en" in templates and (latin >= 2 or cjk == 0):
        langs.append("en")
    if not langs:
        langs = [key for key in templates if key in {"zh", "en"}] or list(templates)
    return [lang for lang in langs if lang in templates]


def _google_fallback_urls(primary: str, lang: str) -> list[str]:
    urls = [primary]
    if lang != "zh":
        return urls
    if "gl=CN" in primary:
        urls.append(
            primary.replace("gl=CN", "gl=HK").replace("ceid=CN:zh-Hans", "ceid=HK:zh-Hans")
        )
        urls.append(
            primary.replace("gl=CN", "gl=US").replace("ceid=CN:zh-Hans", "ceid=US:zh-Hans")
        )
    elif "gl=HK" in primary:
        urls.append(
            primary.replace("gl=HK", "gl=US").replace("ceid=HK:zh-Hans", "ceid=US:zh-Hans")
        )
    return urls


def _fetch_google_query(urls: list[str], source: str, limit: int) -> list[NewsItem]:
    last_error: Exception | None = None
    for url in urls:
        try:
            return fetch_rss(url, source, limit, browser=True)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    raise last_error or RuntimeError("google news failed")


def fetch_google_news(queries: list[str], limit: int) -> tuple[list[NewsItem], list[str]]:
    templates = load_yaml("sources.yml").get("google_news") or {}
    workers = int(load_yaml("budgets.yml").get("google_news_workers") or 2)
    tasks: list[Callable[[], list[NewsItem]]] = []
    labels: list[str] = []
    for query in queries:
        encoded = quote_plus(query)
        for lang in google_editions_for_query(query, templates):
            template = str(templates[lang])
            primary = template.format(query=encoded)
            labels.append(f"gnews:{lang}:{query}")
            tasks.append(
                lambda u=_google_fallback_urls(primary, lang), q=query: _fetch_google_query(
                    u, f"Google News / {q}", limit
                )
            )
    raw = run_parallel(tasks, workers=max(1, workers))
    items: list[NewsItem] = []
    errors: list[str] = []
    limited = 0
    for label, result in zip(labels, raw, strict=True):
        if isinstance(result, Exception):
            detail = compact_http_error(result)
            if "503" in detail or "429" in detail:
                limited += 1
            else:
                errors.append(f"{label}: {detail}")
        else:
            items.extend(result)
    if limited:
        errors.insert(0, f"Google News {limited}/{len(labels)} 个请求限流（503/429），已降并发并重试")
    return items, errors
