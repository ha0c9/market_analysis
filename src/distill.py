from __future__ import annotations

import re
from difflib import SequenceMatcher

from src.models import NewsItem
from src.settings import load_yaml
from src.timeutil import now_utc, within_lookback


def _normalize_title(title: str) -> str:
    text = title.lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def _keyword_score(item: NewsItem, keywords: list[str]) -> float:
    blob = f"{item.title} {item.snippet}".lower()
    if not keywords:
        return 1.0
    score = 0.0
    for keyword in keywords:
        token = keyword.lower().strip()
        if not token:
            continue
        if token in item.title.lower():
            score += 3.0
        elif token in blob:
            score += 1.0
    return score


def distill_news(items: list[NewsItem], keywords: list[str], lookback_hours: int) -> list[NewsItem]:
    budgets = load_yaml("budgets.yml")
    cap = int(budgets.get("max_news_kept") or 60)
    min_keep = int(budgets.get("min_news_kept") or 8)
    scored_rows: list[NewsItem] = []
    seen_titles: list[str] = []
    now = now_utc()
    for item in items:
        if not item.title:
            continue
        normalized = _normalize_title(item.title)
        if not normalized:
            continue
        duplicate = False
        for previous in seen_titles:
            if normalized == previous or SequenceMatcher(None, normalized, previous).ratio() >= 0.92:
                duplicate = True
                break
        if duplicate:
            continue
        scored = item.model_copy(deep=True)
        scored.score = _keyword_score(scored, keywords)
        if item.source.startswith("Google News"):
            scored.score = max(scored.score, 1.0)
        elif keywords and scored.score <= 0:
            continue
        seen_titles.append(normalized)
        scored_rows.append(scored)

    recent = [item for item in scored_rows if within_lookback(item, lookback_hours, now=now)]
    recent.sort(key=lambda row: (row.score, row.publishedAt), reverse=True)
    if len(recent) >= min_keep:
        return recent[:cap]

    scored_rows.sort(key=lambda row: (row.score, row.publishedAt), reverse=True)
    extra: list[NewsItem] = []
    seen = {id(item) for item in recent}
    for item in scored_rows:
        if id(item) in seen:
            continue
        extra.append(item)
        if len(recent) + len(extra) >= min_keep:
            break
    return (recent + extra)[:cap]

