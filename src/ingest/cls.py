from __future__ import annotations

from datetime import datetime, timezone
from hashlib import md5, sha1
from typing import Any
from urllib.parse import urlencode

from src.ingest.news import BROWSER_UA, classify_source, compact_http_error, http_client
from src.models import NewsItem
from src.settings import load_yaml
from src.timeutil import isoformat

CLS_ROLL = "https://www.cls.cn/v1/roll/get_roll_list"
CLS_DETAIL = "https://www.cls.cn/detail/{id}"


def cls_sign(params: dict[str, str]) -> str:
    """CLS web API: sign = md5(sha1(sorted query string)). Sign itself is not hashed."""
    raw = urlencode(sorted(params.items()))
    digest = sha1(raw.encode("utf-8")).hexdigest()
    return md5(digest.encode("utf-8")).hexdigest()


def is_cls_red(row: dict[str, Any]) -> bool:
    """标红/重点电报：level A/B，或 bold=1。"""
    level = str(row.get("level") or "").strip().upper()
    if level in {"A", "B"}:
        return True
    try:
        return int(row.get("bold") or 0) == 1
    except (TypeError, ValueError):
        return False


def _title_from_row(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "").strip()
    if title:
        return title
    brief = str(row.get("brief") or row.get("content") or "").strip()
    brief = brief.replace("\n", " ")
    if brief.startswith("【") and "】" in brief:
        return brief[1 : brief.index("】")]
    return brief[:80]


def parse_cls_roll(payload: dict[str, Any], limit: int = 24) -> list[NewsItem]:
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = (data or {}).get("roll_data") or []
    items: list[NewsItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _title_from_row(row)
        if not title:
            continue
        red = is_cls_red(row)
        snippet = str(row.get("brief") or row.get("content") or title).strip()
        ctime = row.get("ctime")
        published = ""
        try:
            published = isoformat(datetime.fromtimestamp(int(ctime), tz=timezone.utc))
        except (TypeError, ValueError, OSError):
            published = ""
        article_id = row.get("id")
        url = str(row.get("shareurl") or "").strip()
        if article_id:
            url = CLS_DETAIL.format(id=article_id)
        item = NewsItem(
            title=title,
            source="财联社标红" if red else "财联社电报",
            url=url,
            publishedAt=published,
            snippet=snippet[:400],
            highlight=red,
        )
        class_name, weight = classify_source(item.source, item.url, item.title)
        item.sourceClass = class_name
        item.sourceWeight = max(weight, 2.6) if red else weight
        items.append(item)
        if len(items) >= limit:
            break
    return items


def fetch_cls_telegraph(limit: int | None = None) -> tuple[list[NewsItem], list[str]]:
    cap = limit if limit is not None else int(load_yaml("budgets.yml").get("max_items_per_source") or 24)
    params = {
        "app": "CailianpressWeb",
        "category": "",
        "last_time": str(int(datetime.now(tz=timezone.utc).timestamp())),
        "os": "web",
        "refresh_type": "1",
        "rn": str(max(cap, 20)),
        "sv": "8.4.6",
    }
    params["sign"] = cls_sign({key: value for key, value in params.items() if key != "sign"})
    try:
        with http_client(timeout=18.0, browser=True) as client:
            response = client.get(
                CLS_ROLL,
                params=params,
                headers={
                    "User-Agent": BROWSER_UA,
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.cls.cn/telegraph",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return [], [f"cls telegraph: {compact_http_error(exc)}"]
    if not isinstance(payload, dict):
        return [], ["cls telegraph: invalid payload"]
    if payload.get("errno") not in (0, "0", None):
        return [], [f"cls telegraph: {payload.get('msg') or payload.get('errno')}"]
    items = parse_cls_roll(payload, limit=cap)
    if not items:
        return [], ["cls telegraph: empty"]
    return items, []
