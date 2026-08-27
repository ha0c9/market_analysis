from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from src.ingest.news import BROWSER_UA, RETRY_STATUSES, compact_http_error, http_client
from src.models import HotSearchItem
from src.settings import load_yaml
from src.timeutil import isoformat, now_utc

HOT_BAND = "https://weibo.com/ajax/statuses/hot_band"
HOT_SEARCH = "https://weibo.com/ajax/side/hotSearch"
WEIBO_REFERER = "https://weibo.com/hot/"
SEARCH_URL = "https://s.weibo.com/weibo?q={query}&Refer=top"

FINANCE_CATEGORIES = ("财经", "经济", "金融", "股市", "基金", "证券", "商业")
MARKET_TOKENS = (
    "A股",
    "港股",
    "美股",
    "股市",
    "上证",
    "深证",
    "创业板",
    "科创板",
    "北向",
    "南向",
    "央行",
    "降准",
    "降息",
    "加息",
    "IPO",
    "金价",
    "黄金",
    "原油",
    "人民币",
    "汇率",
    "财报",
    "指数",
    "成交额",
    "成交量",
    "牛市",
    "熊市",
    "券商",
    "保险股",
    "银行股",
    "半导体",
    "芯片",
    "存储芯片",
    "新能源车",
)


def _unix_iso(value: Any) -> str:
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    try:
        return isoformat(datetime.fromtimestamp(stamp, tz=timezone.utc))
    except (OSError, OverflowError, ValueError):
        return ""


def _topic_url(word: str, scheme: str = "") -> str:
    query = (scheme or "").strip() or f"#{word.strip()}#"
    return SEARCH_URL.format(query=quote(query, safe=""))


def _rank(row: dict[str, Any], fallback: int) -> int:
    try:
        realpos = int(row.get("realpos"))
        if realpos > 0:
            return realpos
    except (TypeError, ValueError):
        pass
    try:
        rank = int(row.get("rank"))
        if rank >= 0:
            return rank if rank >= 1 else rank + 1
    except (TypeError, ValueError):
        pass
    return fallback


def _heat(row: dict[str, Any]) -> int | None:
    try:
        value = int(row.get("num"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def rows_from_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return [], ""
    if isinstance(data.get("band_list"), list) and data["band_list"]:
        return [row for row in data["band_list"] if isinstance(row, dict)], "hot_band"
    realtime = data.get("realtime")
    if isinstance(realtime, list) and realtime:
        return [row for row in realtime if isinstance(row, dict)], "hotSearch"
    return [], ""


def parse_hot_rows(rows: list[dict[str, Any]], *, fetched_at: str) -> list[HotSearchItem]:
    items: list[HotSearchItem] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if row.get("is_ad"):
            continue
        word = str(row.get("word") or row.get("note") or "").strip().strip("#")
        if not word or word in seen:
            continue
        seen.add(word)
        scheme = str(row.get("word_scheme") or "").strip()
        items.append(
            HotSearchItem(
                rank=_rank(row, index),
                word=word,
                category=str(row.get("category") or "").strip(),
                heat=_heat(row),
                label=str(row.get("label_name") or row.get("icon_desc") or "").strip(),
                url=_topic_url(word, scheme),
                onboardAt=_unix_iso(row.get("onboard_time") or row.get("onboard_ts")),
                fetchedAt=fetched_at,
            )
        )
    return items


def _token_hit(token: str, blob: str) -> bool:
    needle = (token or "").strip()
    if len(needle) < 2:
        return False
    return needle.lower() in blob.lower()


def classify_hot_item(item: HotSearchItem, keywords: list[str]) -> str:
    category = item.category or ""
    blob = f"{item.word} {category}"
    if any(token in category for token in FINANCE_CATEGORIES):
        return "finance"
    if any(_token_hit(token, blob) for token in keywords):
        return "focus"
    if any(token in blob for token in MARKET_TOKENS):
        return "market"
    return ""


def is_fresh(item: HotSearchItem, *, max_age_hours: int, now: datetime | None = None) -> bool:
    """Keep live-board items with no onboard time; drop topics older than the window."""
    if not item.onboardAt:
        return True
    current = now or now_utc()
    try:
        onboard = datetime.fromisoformat(item.onboardAt.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (current - onboard).total_seconds() <= max_age_hours * 3600 + 300


def select_finance_hot(
    items: list[HotSearchItem],
    keywords: list[str],
    *,
    max_age_hours: int,
    limit: int,
    now: datetime | None = None,
) -> list[HotSearchItem]:
    kept: list[HotSearchItem] = []
    for item in items:
        if not is_fresh(item, max_age_hours=max_age_hours, now=now):
            continue
        match = classify_hot_item(item, keywords)
        if not match:
            continue
        item = item.model_copy(update={"match": match})
        kept.append(item)
    kept.sort(key=lambda row: (0 if row.match == "finance" else 1, row.rank or 999, -(row.heat or 0)))
    return kept[:limit]


def _get_json(url: str, attempts: int) -> dict[str, Any]:
    last_error: Exception | None = None
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": WEIBO_REFERER,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    for attempt in range(attempts):
        try:
            with http_client(timeout=12.0, browser=True) as client:
                response = client.get(url, headers=headers)
                if response.status_code in RETRY_STATUSES | {403, 418} and attempt < attempts - 1:
                    time.sleep(0.8 * (attempt + 1))
                    last_error = httpx.HTTPStatusError(
                        f"{response.status_code} {response.reason_phrase} for url '{url}'",
                        request=response.request,
                        response=response,
                    )
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("invalid payload")
                if payload.get("ok") not in (1, "1", True, None):
                    raise ValueError(str(payload.get("msg") or payload.get("ok") or "not ok"))
                return payload
        except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise
    raise last_error or RuntimeError(f"failed to fetch {url}")


def fetch_weibo_finance_hot(
    keywords: list[str],
    lookback_hours: int,
    *,
    now: datetime | None = None,
) -> tuple[list[HotSearchItem], list[str], bool]:
    """Public Weibo hot-search snapshot, filtered to finance/market/focus. Never fails the job."""
    budgets = load_yaml("budgets.yml")
    attempts = max(1, int(budgets.get("news_retries") or 2) + 1)
    limit = int(budgets.get("weibo_hot_max") or 16)
    max_age = min(int(lookback_hours or 36), int(budgets.get("weibo_max_age_hours") or 18))
    fetched_at = isoformat(now or now_utc())
    last_error = ""
    for url in (HOT_BAND, HOT_SEARCH):
        try:
            payload = _get_json(url, attempts)
            rows, source = rows_from_payload(payload)
            parsed = parse_hot_rows(rows, fetched_at=fetched_at)
            if not parsed:
                last_error = f"weibo {source or url}: empty"
                continue
            kept = select_finance_hot(
                parsed,
                keywords,
                max_age_hours=max_age,
                limit=limit,
                now=now,
            )
            return kept, [], True
        except Exception as exc:  # noqa: BLE001
            last_error = f"weibo: {compact_http_error(exc)}"
            continue
    return [], [last_error or "weibo: unavailable"], False
