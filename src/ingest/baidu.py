from __future__ import annotations

from typing import Any

from src.ingest.news import BROWSER_UA, compact_http_error, http_client
from src.models import HeatItem
from src.timeutil import isoformat, now_utc

BAIDU_BOARD = "https://top.baidu.com/api/board"


def _walk_words(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            word = str(node.get("word") or "").strip()
            if word:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


def parse_baidu_board(payload: Any, *, channel: str = "web", tab: str = "") -> list[HeatItem]:
    rows: list[HeatItem] = []
    seen: set[str] = set()
    for item in _walk_words(payload):
        word = str(item.get("word") or "").strip()
        if not word or word in seen:
            continue
        seen.add(word)
        rank = item.get("index")
        try:
            rank_i = int(rank) if rank is not None else len(rows) + 1
        except (TypeError, ValueError):
            rank_i = len(rows) + 1
        url = str(item.get("url") or "").strip()
        tag = str(item.get("hotTag") or "")
        detail = "百度财经热搜" if tab == "finance" else "百度热搜"
        if tag and tag not in {"0", "null"}:
            detail = f"{detail} · 热标 {tag}"
        rows.append(
            HeatItem(
                channel="web",
                name=word,
                detail=detail,
                url=url,
                rank=rank_i,
            )
        )
    peak = max((item.rank or 1) for item in rows) if rows else 1
    for item in rows:
        # Lower index = hotter on Baidu.
        item.heatScore = max(0.15, 1.0 - ((item.rank or 1) - 1) / max(peak, 1))
    return rows


def fetch_baidu_hot(limit: int = 16) -> tuple[list[HeatItem], list[str]]:
    errors: list[str] = []
    rows: list[HeatItem] = []
    seen: set[str] = set()
    for tab in ("realtime", "finance"):
        try:
            with http_client(timeout=18.0, browser=True) as client:
                response = client.get(
                    BAIDU_BOARD,
                    params={"platform": "wise", "tab": tab},
                    headers={"User-Agent": BROWSER_UA, "Referer": "https://top.baidu.com/board"},
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"baidu {tab}: {compact_http_error(exc)}")
            continue
        parsed = parse_baidu_board(payload, tab=tab)
        if not parsed:
            errors.append(f"baidu {tab}: empty")
            continue
        cap = 8 if tab == "finance" else 10
        for item in parsed[:cap]:
            if item.name in seen:
                continue
            seen.add(item.name)
            rows.append(item)
    now = isoformat(now_utc())
    for index, item in enumerate(rows[:limit], start=1):
        item.rank = index
        item.asOf = now
    return rows[:limit], errors
