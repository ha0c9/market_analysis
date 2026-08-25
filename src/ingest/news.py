from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from typing import Callable, TypeVar
from urllib.parse import quote_plus
import re

import feedparser
import httpx

from src import USER_AGENT
from src.models import NewsItem
from src.settings import load_yaml
from src.timeutil import isoformat, parse_datetime

T = TypeVar("T")


def http_client(timeout: float | None = None) -> httpx.Client:
    budgets = load_yaml("budgets.yml")
    seconds = timeout if timeout is not None else float(budgets.get("http_timeout_seconds") or 12)
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        timeout=httpx.Timeout(seconds, connect=8.0),
        follow_redirects=True,
    )


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", unescape(cleaned)).strip()


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


def fetch_rss(url: str, source: str, limit: int) -> list[NewsItem]:
    with http_client() as client:
        response = client.get(url)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
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
            NewsItem(
                title=strip_html(str(getattr(entry, "title", "") or "")),
                source=source,
                url=str(getattr(entry, "link", "") or ""),
                publishedAt=isoformat(dt),
                snippet=summary[:400],
            )
        )
    return [item for item in items if item.title]


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
            errors.append(f"RSS {feed.get('id')}: {result}")
        else:
            items.extend(result)
    return items, errors


def fetch_google_news(queries: list[str], limit: int) -> tuple[list[NewsItem], list[str]]:
    templates = (load_yaml("sources.yml").get("google_news") or {})
    tasks: list[Callable[[], list[NewsItem]]] = []
    labels: list[str] = []
    for query in queries:
        encoded = quote_plus(query)
        for lang, template in templates.items():
            url = str(template).format(query=encoded)
            labels.append(f"gnews:{lang}:{query}")
            tasks.append(lambda u=url, q=query: fetch_rss(u, f"Google News / {q}", limit))
    raw = run_parallel(tasks, workers=int(load_yaml("budgets.yml").get("concurrent_fetches") or 8))
    items: list[NewsItem] = []
    errors: list[str] = []
    for label, result in zip(labels, raw, strict=True):
        if isinstance(result, Exception):
            errors.append(f"{label}: {result}")
        else:
            items.extend(result)
    return items, errors
