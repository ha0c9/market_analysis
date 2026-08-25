from __future__ import annotations

from datetime import datetime, timezone

from src.ingest.news import http_client, run_parallel
from src.models import QuoteRow
from src.settings import load_yaml
from src.timeutil import isoformat, parse_datetime


def normalize_symbol(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    upper = text.upper()
    if upper.endswith(".SH"):
        return "sh" + upper[:-3]
    if upper.endswith(".SZ"):
        return "sz" + upper[:-3]
    if text[:2].lower() in {"sh", "sz"} and text[2:].isdigit():
        return text[:2].lower() + text[2:]
    if text.isdigit() and len(text) == 6:
        if text.startswith(("6", "5", "9")):
            return "sh" + text
        return "sz" + text
    return text


def _is_a_share(symbol: str) -> bool:
    return symbol[:2] in {"sh", "sz"} and symbol[2:].isdigit()


def parse_tencent_body(body: str) -> list[QuoteRow]:
    rows: list[QuoteRow] = []
    for chunk in body.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        _, _, quoted = chunk.partition("=")
        payload = quoted.strip().strip('";')
        fields = payload.split("~")
        if len(fields) < 33:
            continue
        name = fields[1]
        code = fields[2]
        try:
            price = float(fields[3]) if fields[3] else None
            change_pct = float(fields[32]) if fields[32] else None
        except ValueError:
            price, change_pct = None, None
        as_of = isoformat(parse_datetime(fields[30] if len(fields) > 30 else ""))
        prefix = "sh" if chunk.startswith("v_sh") else "sz" if chunk.startswith("v_sz") else ""
        symbol = f"{prefix}{code}" if prefix else code
        rows.append(
            QuoteRow(symbol=symbol, name=name, price=price, changePct=change_pct, asOf=as_of)
        )
    return rows


def fetch_tencent(symbols: list[str]) -> list[QuoteRow]:
    codes = [symbol for symbol in symbols if _is_a_share(symbol)]
    if not codes:
        return []
    query = ",".join(codes)
    with http_client() as client:
        response = client.get("https://qt.gtimg.cn/q=" + query)
        response.raise_for_status()
        text = response.content.decode("gbk", errors="replace")
    return parse_tencent_body(text)


def fetch_yahoo(symbol: str) -> QuoteRow:
    encoded = symbol.replace("^", "%5E")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=1mo"
    with http_client() as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    result = payload["chart"]["result"][0]
    meta = result.get("meta") or {}
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    valid = [float(value) for value in closes if value is not None]
    price = meta.get("regularMarketPrice")
    if price is None and valid:
        price = valid[-1]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    change_pct = None
    if price is not None and prev:
        change_pct = (float(price) / float(prev) - 1.0) * 100
    change_5d = None
    if len(valid) >= 6:
        change_5d = (valid[-1] / valid[-6] - 1.0) * 100
    timestamp = result.get("meta", {}).get("regularMarketTime")
    as_of = ""
    if timestamp:
        as_of = isoformat(datetime.fromtimestamp(int(timestamp), tz=timezone.utc))
    return QuoteRow(
        symbol=symbol,
        name=str(meta.get("shortName") or meta.get("symbol") or symbol),
        price=float(price) if price is not None else None,
        changePct=change_pct,
        changePct5d=change_5d,
        asOf=as_of,
    )


def fetch_quotes(symbols: list[str]) -> tuple[list[QuoteRow], list[str]]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = normalize_symbol(raw)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        unique.append(symbol)
    a_share = [symbol for symbol in unique if _is_a_share(symbol)]
    rest = [symbol for symbol in unique if not _is_a_share(symbol)]
    rows: list[QuoteRow] = []
    errors: list[str] = []
    if a_share:
        try:
            rows.extend(fetch_tencent(a_share))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tencent quotes: {exc}")
    tasks = [lambda s=symbol: fetch_yahoo(s) for symbol in rest]
    for symbol, result in zip(rest, run_parallel(tasks, workers=6), strict=True):
        if isinstance(result, Exception):
            errors.append(f"yahoo {symbol}: {result}")
        else:
            rows.append(result)
    return rows, errors


def snapshot_from_rows(rows: list[QuoteRow], source: str) -> dict:
    sources = load_yaml("sources.yml")
    benchmark_ids = {row["symbol"] for row in sources.get("benchmarks") or []}
    sector_ids = {row["symbol"] for row in sources.get("sector_quotes") or []}
    as_of = next((row.asOf for row in rows if row.asOf), "")
    return {
        "asOf": as_of,
        "delayed": True,
        "source": source,
        "benchmarks": [
            row.model_dump()
            for row in rows
            if row.symbol in benchmark_ids or row.symbol in {"sh000001", "sh000300", "sz399006", "^GSPC", "^IXIC", "^HSI"}
        ],
        "sectors": [row.model_dump() for row in rows if row.symbol in sector_ids],
        "tickers": [
            row.model_dump()
            for row in rows
            if row.symbol not in benchmark_ids and row.symbol not in sector_ids
        ],
    }
