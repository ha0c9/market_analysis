from __future__ import annotations

from src.ingest.quotes import normalize_symbol
from src.models import QuoteRow
from src.settings import load_yaml


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for item in values:
        text = str(item).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        rows.append(text)
    return rows


def tape_sector_symbols() -> list[str]:
    rows = load_yaml("sources.yml").get("tape_sectors") or []
    symbols: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = normalize_symbol(str(row.get("symbol") or ""))
        if symbol:
            symbols.append(symbol)
    return symbols


def merge_tape_etfs(etfs: list[str], limit: int | None = None) -> list[str]:
    cap = limit if limit is not None else int(load_yaml("budgets.yml").get("max_tickers") or 24)
    return _dedupe([*tape_sector_symbols(), *[normalize_symbol(item) for item in etfs]])[:cap]


def _sector_label(row: QuoteRow) -> str:
    name = (row.name or "").strip()
    for suffix in ("ETF华宝", "ETF国联安", "ETF华泰柏瑞", "ETF南方", "ETF国泰", "ETF"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip() or row.symbol


def mover_news_queries(quotes: list[QuoteRow], limit: int = 4) -> list[str]:
    """Turn yesterday/last-session sector moves into news searches."""
    watch = set(tape_sector_symbols())
    ranked = sorted(
        [
            row
            for row in quotes
            if row.symbol in watch and row.changePct is not None
        ],
        key=lambda row: abs(float(row.changePct or 0)),
        reverse=True,
    )
    queries = [
        "昨日 A股 涨停 板块 主线 复盘",
        "昨日 领涨 行业 涨停潮",
        "今日 盘前 热点 主线",
    ]
    for row in ranked:
        move = abs(float(row.changePct or 0))
        if move < 0.8:
            continue
        label = _sector_label(row)
        if not label:
            continue
        queries.append(f"{label} 涨停 板块 原因")
        if len(queries) >= 3 + limit:
            break
    return _dedupe(queries)
