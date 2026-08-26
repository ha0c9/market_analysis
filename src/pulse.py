from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from src.models import NewsItem, QuoteRow
from src.timeutil import BEIJING, parse_datetime


def _beijing_date(value: str) -> str:
    dt = parse_datetime(value)
    if not dt:
        return ""
    return dt.astimezone(BEIJING).strftime("%Y-%m-%d")


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _trend_label(values: list[float], *, up: str, down: str, flat: str = "stable") -> str:
    if len(values) < 4:
        return "unknown"
    recent = _mean(values[-3:])
    prior = _mean(values[:-3])
    if recent is None or prior is None or prior == 0:
        return "unknown"
    ratio = recent / prior
    if ratio >= 1.12:
        return up
    if ratio <= 0.88:
        return down
    return flat


def _pick_volume_row(quotes: list[QuoteRow]) -> QuoteRow | None:
    prefer = ("sh000001", "sh000300", "sz399006")
    by_symbol = {row.symbol: row for row in quotes if row.series}
    for symbol in prefer:
        if symbol in by_symbol:
            return by_symbol[symbol]
    return next((row for row in quotes if row.series), None)


def _volume_block(quotes: list[QuoteRow]) -> dict[str, Any]:
    row = _pick_volume_row(quotes)
    if not row:
        return {
            "symbol": "",
            "name": "",
            "series": [],
            "latestVolumeVsAvg": None,
            "trend": "unknown",
            "note": "未取到足够的历史成交量，无法把量能串成线。",
        }
    volumes = [float(point["volume"]) for point in row.series if point.get("volume")]
    trend = _trend_label(volumes, up="expanding", down="contracting")
    vs_avg = row.volumeVsAvg
    if trend == "expanding":
        note = f"{row.name or row.symbol}近段成交量高于前段，量能在放大，不宜只看最新一日。"
    elif trend == "contracting":
        note = f"{row.name or row.symbol}近段成交量低于前段，量能在收缩。"
    elif trend == "stable":
        note = f"{row.name or row.symbol}近两周成交量相对平稳，单日放量/缩量需要放回序列里看。"
    else:
        note = f"{row.name or row.symbol}历史成交量点数不足，趋势待观察。"
    if vs_avg:
        note += f" 最新一日成交约为前段均值的 {vs_avg:.2f} 倍。"
    return {
        "symbol": row.symbol,
        "name": row.name,
        "series": row.series,
        "latestVolumeVsAvg": vs_avg,
        "trend": trend,
        "note": note,
    }


def _northbound_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    net_available = any(row.get("netDealAmt") is not None for row in rows)
    amounts = [float(row["dealAmtYi"]) for row in rows if row.get("dealAmtYi") is not None]
    trend = _trend_label(amounts, up="more_active", down="less_active")
    if not rows:
        note = "未取到北向成交额序列。"
    elif not net_available:
        if trend == "more_active":
            note = "北向成交额近段高于前段，外资交易更活跃；这是成交额而非净买入。"
        elif trend == "less_active":
            note = "北向成交额近段低于前段，外资交易活跃度回落；不能据此判断净流入方向。"
        elif trend == "stable":
            note = "北向成交额近两周大体平稳。交易所已不再披露实时净买入，只能看活跃度与上证对照。"
        else:
            note = "北向成交额点数不足。交易所已不再披露实时净买入，序列用成交额代替。"
    else:
        note = "北向净买入与成交额均可对照上证，仍应按时间线而不是最新一日解读。"
    return {
        "series": rows,
        "netBuyAvailable": net_available,
        "trend": trend,
        "unit": "亿元成交额",
        "note": note,
    }


def _sentiment_block(news: list[NewsItem]) -> dict[str, Any]:
    buckets: dict[str, list[NewsItem]] = defaultdict(list)
    classes: Counter[str] = Counter()
    for item in news:
        classes[item.sourceClass or "other"] += 1
        day = _beijing_date(item.publishedAt)
        if day:
            buckets[day].append(item)
    series = []
    for day in sorted(buckets):
        items = buckets[day]
        series.append(
            {
                "date": day,
                "score": round(sum(item.score for item in items), 2),
                "count": len(items),
                "officialCount": sum(1 for item in items if item.sourceClass == "official"),
            }
        )
    scores = [float(row["score"]) for row in series]
    trend = _trend_label(scores, up="warming", down="cooling")
    if trend == "warming":
        note = "按日加权的新闻热度近段高于前段，情绪在升温；已按官方>主流媒体>博客加权。"
    elif trend == "cooling":
        note = "按日加权的新闻热度近段低于前段，情绪在降温。"
    elif trend == "stable":
        note = "新闻情绪近几日大体平稳，需结合量能和北向成交额一起看。"
    else:
        note = "可分日的新闻偏少，情绪趋势只能作参考。"
    return {
        "series": series,
        "trend": trend,
        "sourceMix": dict(classes),
        "note": note,
    }


def build_market_pulse(
    news: list[NewsItem],
    quotes: list[QuoteRow],
    northbound: list[dict[str, Any]],
) -> dict[str, Any]:
    volume = _volume_block(quotes)
    flows = _northbound_block(northbound)
    sentiment = _sentiment_block(news)
    notes = [block["note"] for block in (volume, flows, sentiment) if block.get("note")]
    summary = " ".join(notes)
    as_of = ""
    if volume.get("series"):
        as_of = str(volume["series"][-1].get("date") or "")
    if not as_of and flows.get("series"):
        as_of = str(flows["series"][-1].get("date") or "")
    return {
        "asOf": as_of,
        "volume": volume,
        "northbound": flows,
        "sentiment": sentiment,
        "notes": notes,
        "summary": summary,
    }
