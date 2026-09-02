from __future__ import annotations

from src.ingest.quotes import normalize_symbol
from src.models import HeatBoard, HeatItem, HotSearchItem, NewsItem, QuoteRow
from src.tape_scan import _sector_label, tape_sector_symbols
from src.timeutil import isoformat, now_utc


def _from_weibo(items: list[HotSearchItem], limit: int = 12) -> list[HeatItem]:
    ranked = sorted(
        items,
        key=lambda item: (
            0 if item.focusEvent else 1,
            -(item.attention or 0),
            -(item.clusterHeat or item.heat or 0),
        ),
    )
    rows: list[HeatItem] = []
    seen: set[str] = set()
    peak = max((item.clusterHeat or item.heat or 1) for item in ranked) if ranked else 1
    for item in ranked:
        name = (item.cluster or item.word or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        heat = item.clusterHeat or item.heat or 0
        rows.append(
            HeatItem(
                channel="social",
                name=name,
                detail=f"微博{item.label or item.category or item.kind or '热搜'}",
                heatScore=min(1.0, heat / max(peak, 1)),
                url=item.url,
                asOf=item.fetchedAt,
                rank=item.rank or len(rows) + 1,
            )
        )
        if len(rows) >= limit:
            break
    return rows


def _from_cls(news: list[NewsItem], limit: int = 8) -> list[HeatItem]:
    rows: list[HeatItem] = []
    for item in news:
        if not item.title:
            continue
        hot = item.highlight or any(token in item.title for token in ("涨停", "涨幅居前", "大涨", "爆发", "热潮"))
        if not hot:
            continue
        rows.append(
            HeatItem(
                channel="news",
                name=item.title[:40],
                detail="财联社标红" if item.highlight else "财联社电报",
                heatScore=1.0 if item.highlight else 0.7,
                url=item.url,
                asOf=item.publishedAt,
            )
        )
        if len(rows) >= limit:
            break
    return rows


def _from_etfs(quotes: list[QuoteRow], limit: int = 8) -> list[HeatItem]:
    watch = set(tape_sector_symbols())
    ranked = sorted(
        [row for row in quotes if row.symbol in watch and row.changePct is not None],
        key=lambda row: abs(float(row.changePct or 0)),
        reverse=True,
    )
    peak = max((abs(float(row.changePct or 0)) for row in ranked), default=1.0) or 1.0
    rows: list[HeatItem] = []
    for index, row in enumerate(ranked[:limit], start=1):
        move = abs(float(row.changePct or 0))
        if move < 0.6:
            continue
        rows.append(
            HeatItem(
                channel="tape",
                name=_sector_label(row),
                detail=row.name,
                heatScore=min(1.0, move / max(peak, 1.0)),
                changePct=row.changePct,
                asOf=row.asOf,
                rank=index,
            )
        )
    return rows


def _from_northbound(rows: list[dict], limit: int = 3) -> list[HeatItem]:
    if not rows:
        return []
    last = rows[-1]
    lead = str(last.get("leadStock") or "").strip()
    detail = f"北向成交额 {last.get('dealAmtYi')} 亿" if last.get("dealAmtYi") is not None else "北向成交额"
    items = [
        HeatItem(
            channel="flow",
            name=lead or "北向成交",
            detail=detail,
            changePct=last.get("leadChangePct"),
            leadName=lead,
            heatScore=0.6,
            asOf=str(last.get("date") or ""),
            rank=1,
        )
    ]
    return items[:limit]


def assemble_heat(
    *,
    boards: list[HeatItem],
    baidu: list[HeatItem],
    weibo: list[HotSearchItem],
    news: list[NewsItem],
    quotes: list[QuoteRow],
    northbound: list[dict],
) -> HeatBoard:
    tape = list(boards) or _from_etfs(quotes)
    items = [
        *tape,
        *_from_weibo(weibo),
        *baidu,
        *_from_cls(news),
        *_from_northbound(northbound),
    ]
    # Prefer sina industry over ETF duplicate names.
    seen: set[tuple[str, str]] = set()
    unique: list[HeatItem] = []
    for item in items:
        key = (item.channel, item.name)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return HeatBoard(
        asOf=isoformat(now_utc()),
        items=unique,
        coverage={
            "tape": any(item.channel == "tape" for item in unique),
            "flow": any(item.channel == "flow" for item in unique),
            "news": any(item.channel == "news" for item in unique),
            "social": any(item.channel == "social" for item in unique),
            "web": any(item.channel == "web" for item in unique),
        },
    )


def heat_keywords(board: HeatBoard, limit: int = 24) -> list[str]:
    names: list[str] = []
    for item in board.items:
        if item.channel == "news":
            continue
        for token in (item.name, item.leadName):
            text = (token or "").strip()
            if text and text not in names:
                names.append(text)
        if len(names) >= limit:
            break
    return names


def heat_news_queries(board: HeatBoard, limit: int = 6) -> list[str]:
    queries = ["昨日 A股 涨停 板块 主线 复盘", "今日 盘前 热点 领涨 行业"]
    seen = set(queries)
    for item in board.items:
        if item.channel != "tape":
            continue
        label = item.name.strip()
        if not label:
            continue
        query = f"{label} 涨停 原因 板块"
        if query not in seen:
            seen.add(query)
            queries.append(query)
        if item.leadName:
            lead_q = f"{item.leadName} 涨停"
            if lead_q not in seen:
                seen.add(lead_q)
                queries.append(lead_q)
        if len(queries) >= limit:
            break
    for item in board.items:
        if item.channel not in {"web", "social"}:
            continue
        if item.heatScore < 0.7 and (item.rank or 99) > 3:
            continue
        query = f"{item.name[:18]} 市场 影响"
        if query not in seen:
            seen.add(query)
            queries.append(query)
        if len(queries) >= limit + 2:
            break
    return queries[: limit + 2]


def opportunities_from_heat(board: HeatBoard, limit: int = 6) -> list:
    from src.models import Opportunity

    rows: list[Opportunity] = []
    seen: set[str] = set()
    for item in board.items:
        if item.channel != "tape":
            continue
        symbol = normalize_symbol(item.leadSymbol)
        name = item.leadName or symbol
        if not symbol or not name or symbol in seen:
            continue
        if not (symbol.startswith(("sh", "sz")) and symbol[2:].isdigit() and len(symbol) == 8):
            continue
        seen.add(symbol)
        move = item.leadChangePct
        thesis = (
            f"盘面热点「{item.name}」领涨{name}。"
            + (f"板块涨跌约 {item.changePct:.2f}%。" if item.changePct is not None else "")
            + (f"领涨股约 {move:.2f}%。" if move is not None else "")
            + "这是行情热度线索，不是基本面结论，需用公告与后续价格验证。"
        )
        rows.append(
            Opportunity(
                symbol=symbol,
                name=name[:40],
                hotspot=item.name[:40],
                thesis=thesis[:400],
                angle="盘面领涨",
                confidence=0.38,
            )
        )
        if len(rows) >= limit:
            break
    return rows
