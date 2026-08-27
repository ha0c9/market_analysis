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

FINANCE_CATEGORIES = ("财经", "经济", "金融", "股市", "基金", "证券")
SKIP_CATEGORIES = (
    "艺人",
    "综艺",
    "剧集",
    "电影",
    "情感",
    "电竞",
    "演出",
    "幽默",
    "读书作家",
    "作品衍生",
    "体育",
)
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
    "股价",
    "市值",
    "跌破",
    "涨停",
    "跌停",
    "成交额",
    "成交量",
    "牛市",
    "熊市",
    "券商",
    "保险股",
    "银行股",
    "半导体",
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


def is_entertainment(item: HotSearchItem) -> bool:
    category = item.category or ""
    return any(token in category for token in SKIP_CATEGORIES)


def classify_hot_item(item: HotSearchItem, keywords: list[str]) -> str:
    if is_entertainment(item):
        return ""
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
    kept.sort(
        key=lambda row: (
            {"finance": 0, "focus": 1, "llm": 2, "market": 3}.get(row.match, 9),
            row.rank or 999,
            -(row.heat or 0),
        )
    )
    return kept[:limit]


def _compact_for_picker(item: HotSearchItem) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "word": item.word,
        "category": item.category,
        "heat": item.heat,
        "label": item.label,
        "onboardAt": item.onboardAt,
    }


def llm_pick_hot_words(
    items: list[HotSearchItem],
    *,
    focus: str,
    keywords: list[str],
    limit: int,
) -> tuple[list[str], str]:
    """Ask the planner model which non-entertainment topics are market-relevant."""
    from src.llm import LLMError, chat, model_debug, parse_json_object, resolve_model
    from src.settings import env

    if not env("AI_API_KEY") or not items:
        return [], ""
    budgets = load_yaml("budgets.yml")
    prompt = {
        "focus": focus,
        "keywords": keywords[:16],
        "topics": [_compact_for_picker(item) for item in items],
        "instructions": (
            "从微博热搜候选里选出可能影响 A股/港股/美股、风险偏好、宏观政策、公司股价或行业景气的话题。"
            "排除明星、综艺、剧集、情感八卦；词里带「财报」但主体是艺人的不要。"
            "科技发布会、AI、汽车价格、就业/减员、公司名+股价、商品价格、灾害对供应链的影响可以保留。"
            f"最多 {limit} 条。只输出 JSON 对象：keep 为数组，每项 word 必须来自 topics，另给 why 短句。"
        ),
    }
    try:
        model = resolve_model("planner")
        print(f"calling weibo-picker {model_debug(model)} candidates={len(items)}", flush=True)
        raw = chat(
            [
                {"role": "system", "content": "You select market-relevant Weibo topics. Return JSON only."},
                {"role": "user", "content": str(prompt)},
            ],
            model=model,
            max_tokens=int(budgets.get("weibo_picker_max_tokens") or 1800),
            timeout=90.0,
        )
        data = parse_json_object(raw)
        keep = data.get("keep") if isinstance(data, dict) else None
        words: list[str] = []
        if isinstance(keep, list):
            for row in keep:
                if isinstance(row, str) and row.strip():
                    words.append(row.strip())
                elif isinstance(row, dict):
                    word = str(row.get("word") or "").strip()
                    if word:
                        words.append(word)
        known = {item.word for item in items}
        picked = [word for word in words if word in known][:limit]
        print(f"weibo-picker ok kept={len(picked)}", flush=True)
        return picked, model
    except (LLMError, ValueError, TypeError) as exc:
        print(f"weibo-picker failed: {exc}", flush=True)
        return [], ""


def merge_hot_items(
    items: list[HotSearchItem],
    keywords: list[str],
    llm_words: list[str],
    *,
    max_age_hours: int,
    limit: int,
    now: datetime | None = None,
) -> list[HotSearchItem]:
    llm_set = {word for word in llm_words if word}
    selected: dict[str, HotSearchItem] = {}
    for item in items:
        if not is_fresh(item, max_age_hours=max_age_hours, now=now):
            continue
        match = classify_hot_item(item, keywords)
        if not match and item.word in llm_set and not is_entertainment(item):
            match = "llm"
        if not match:
            continue
        selected[item.word] = item.model_copy(update={"match": match})
    ranked = list(selected.values())
    ranked.sort(
        key=lambda row: (
            {"finance": 0, "focus": 1, "llm": 2, "market": 3}.get(row.match, 9),
            row.rank or 999,
            -(row.heat or 0),
        )
    )
    return ranked[:limit]


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
    focus: str = "",
    now: datetime | None = None,
) -> tuple[list[HotSearchItem], list[str], bool]:
    """Public Weibo hot-search snapshot. Rules keep 财经; LLM expands market-relevant topics."""
    budgets = load_yaml("budgets.yml")
    attempts = max(1, int(budgets.get("news_retries") or 2) + 1)
    limit = int(budgets.get("weibo_hot_max") or 24)
    max_age = min(int(lookback_hours or 36), int(budgets.get("weibo_max_age_hours") or 24))
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
            fresh = [item for item in parsed if is_fresh(item, max_age_hours=max_age, now=now)]
            candidates = [item for item in fresh if not is_entertainment(item)]
            llm_words, picker_model = llm_pick_hot_words(
                candidates,
                focus=focus,
                keywords=keywords,
                limit=limit,
            )
            kept = merge_hot_items(
                fresh,
                keywords,
                llm_words,
                max_age_hours=max_age,
                limit=limit,
                now=now,
            )
            print(
                f"weibo source={source} board={len(parsed)} fresh={len(fresh)} "
                f"candidates={len(candidates)} kept={len(kept)} llm_words={len(llm_words)}",
                flush=True,
            )
            return kept, [], True
        except Exception as exc:  # noqa: BLE001
            last_error = f"weibo: {compact_http_error(exc)}"
            continue
    return [], [last_error or "weibo: unavailable"], False
