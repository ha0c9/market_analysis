from __future__ import annotations

from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re

from src.models import NewsItem

BEIJING = ZoneInfo("Asia/Shanghai")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    compact = re.sub(r"[^0-9]", "", text)
    if len(compact) >= 14:
        try:
            dt = datetime.strptime(compact[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def parse_beijing_compact(value: str | None) -> datetime | None:
    """Tencent quote timestamps are local China time, not UTC."""
    compact = re.sub(r"[^0-9]", "", value or "")
    if len(compact) < 14:
        return None
    try:
        local = datetime.strptime(compact[:14], "%Y%m%d%H%M%S").replace(tzinfo=BEIJING)
        return local.astimezone(timezone.utc)
    except ValueError:
        return None


def isoformat(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def within_lookback(item: NewsItem, hours: int, now: datetime | None = None) -> bool:
    published = parse_datetime(item.publishedAt)
    if not published:
        return True
    current = now or now_utc()
    return (current - published).total_seconds() <= hours * 3600 + 300
