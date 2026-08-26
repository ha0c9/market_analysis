from __future__ import annotations

from datetime import datetime, timezone

from src.ingest.news import http_client, run_parallel
from src.models import QuoteRow
from src.settings import load_yaml
from src.timeutil import isoformat, parse_beijing_compact


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
        volume = None
        turnover = None
        try:
            if len(fields) > 6 and fields[6]:
                volume = float(fields[6])
            if len(fields) > 37 and fields[37]:
                turnover = float(fields[37])
        except ValueError:
            pass
        as_of = isoformat(parse_beijing_compact(fields[30] if len(fields) > 30 else ""))
        prefix = "sh" if chunk.startswith("v_sh") else "sz" if chunk.startswith("v_sz") else ""
        symbol = f"{prefix}{code}" if prefix else code
        rows.append(
            QuoteRow(
                symbol=symbol,
                name=name,
                price=price,
                changePct=change_pct,
                volume=volume,
                turnover=turnover,
                asOf=as_of,
            )
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


def previous_close_from_yahoo(meta: dict, closes: list[float], price: float | None) -> float | None:
    """Use the prior daily bar, not Yahoo's unstable chartPreviousClose.

    For ^KS11, chartPreviousClose can be an old session close, so the % move
    disagrees with Sina HQ.KOSPI even when the last price is the same.
    """
    if price is not None and len(closes) >= 2:
        last = closes[-1]
        if last and abs(float(price) - last) <= max(0.01, abs(last) * 1e-6):
            return closes[-2]
        return last
    if len(closes) >= 2:
        return closes[-2]
    if len(closes) == 1 and price is not None and abs(float(price) - closes[0]) > max(0.01, abs(closes[0]) * 1e-6):
        return closes[0]
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    try:
        return float(prev) if prev is not None else None
    except (TypeError, ValueError):
        return None


def to_yahoo_symbol(symbol: str) -> str:
    if _is_a_share(symbol):
        code = symbol[2:]
        return f"{code}.SS" if symbol.startswith("sh") else f"{code}.SZ"
    return symbol


def bars_from_yahoo_chart(timestamps: list, closes: list, volumes: list, limit: int = 15) -> list[dict]:
    points: list[dict] = []
    prev_close: float | None = None
    for stamp, close, volume in zip(timestamps, closes, volumes, strict=False):
        if close is None:
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        change = None
        if prev_close:
            change = (value / prev_close - 1.0) * 100
        date = ""
        try:
            date = datetime.fromtimestamp(int(stamp), tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            date = str(stamp)
        vol = None
        try:
            vol = float(volume) if volume is not None else None
        except (TypeError, ValueError):
            vol = None
        points.append(
            {
                "date": date,
                "close": round(value, 4),
                "volume": vol,
                "changePct": None if change is None else round(change, 3),
            }
        )
        prev_close = value
    return drop_volume_outliers(points[-limit:])


def drop_volume_outliers(points: list[dict], max_ratio: float = 4.0) -> list[dict]:
    """Yahoo's in-progress daily bar for A-share indices uses a different volume unit.

    Completed SSE bars are ~5e5; the live bar can be ~1e9–4e9, which produced
    bogus '7422x average' notes. Drop only extreme high outliers; quiet sessions stay.
    """
    prior = [float(row["volume"]) for row in points[:-1] if row.get("volume")]
    if len(prior) < 3:
        return points
    ordered = sorted(prior)
    median = ordered[len(ordered) // 2]
    if median <= 0:
        return points
    ceiling = median * max_ratio
    cleaned: list[dict] = []
    for row in points:
        item = dict(row)
        try:
            volume = float(item["volume"]) if item.get("volume") is not None else None
        except (TypeError, ValueError):
            volume = None
        if volume is not None and volume > ceiling:
            item["volume"] = None
        cleaned.append(item)
    return cleaned


def volume_vs_average(points: list[dict]) -> float | None:
    volumes = [float(row["volume"]) for row in points if row.get("volume")]
    if len(volumes) < 4:
        return None
    last = volumes[-1]
    prior = volumes[:-1]
    avg = sum(prior) / len(prior)
    if avg <= 0:
        return None
    ratio = last / avg
    if ratio > 4.0 or ratio < 0.15:
        return None
    return round(ratio, 3)


def fetch_yahoo(symbol: str, *, display_symbol: str = "") -> QuoteRow:
    encoded = to_yahoo_symbol(symbol).replace("^", "%5E")
    limit = int(load_yaml("budgets.yml").get("series_days") or 15)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=1mo"
    with http_client() as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    result = payload["chart"]["result"][0]
    meta = result.get("meta") or {}
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    timestamps = result.get("timestamp") or []
    valid = [float(value) for value in closes if value is not None]
    price = meta.get("regularMarketPrice")
    if price is None and valid:
        price = valid[-1]
    price_f = float(price) if price is not None else None
    prev = previous_close_from_yahoo(meta, valid, price_f)
    change_pct = None
    if price_f is not None and prev:
        change_pct = (price_f / float(prev) - 1.0) * 100
    change_5d = None
    if len(valid) >= 6:
        change_5d = (valid[-1] / valid[-6] - 1.0) * 100
    series = bars_from_yahoo_chart(timestamps, closes, volumes, limit=limit)
    timestamp = result.get("meta", {}).get("regularMarketTime")
    as_of = ""
    if timestamp:
        as_of = isoformat(datetime.fromtimestamp(int(timestamp), tz=timezone.utc))
    last_volume = None
    if series and series[-1].get("volume") is not None:
        last_volume = float(series[-1]["volume"])
    return QuoteRow(
        symbol=display_symbol or symbol,
        name=str(meta.get("shortName") or meta.get("symbol") or symbol),
        price=price_f,
        changePct=change_pct,
        changePct5d=change_5d,
        volume=last_volume,
        volumeVsAvg=volume_vs_average(series),
        asOf=as_of,
        series=series,
    )


def _overlay_history(row: QuoteRow) -> QuoteRow:
    try:
        history = fetch_yahoo(row.symbol, display_symbol=row.symbol)
    except Exception:  # noqa: BLE001
        return row
    row.series = history.series
    row.changePct5d = history.changePct5d
    row.volumeVsAvg = history.volumeVsAvg
    if row.volume is None:
        row.volume = history.volume
    return row


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
            tencent_rows = fetch_tencent(a_share)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tencent quotes: {exc}")
            tencent_rows = []
        prefer = {"sh000001", "sh000300", "sz399006"}
        overlay = [row for row in tencent_rows if row.symbol in prefer]
        overlay += [row for row in tencent_rows if row.symbol not in prefer][:8]
        hist = run_parallel([lambda r=row: _overlay_history(r) for row in overlay], workers=6)
        merged: dict[str, QuoteRow] = {row.symbol: row for row in tencent_rows}
        for row, result in zip(overlay, hist, strict=True):
            if isinstance(result, Exception):
                errors.append(f"yahoo history {row.symbol}: {result}")
            else:
                merged[row.symbol] = result
        rows.extend(merged[symbol] for symbol in [row.symbol for row in tencent_rows] if symbol in merged)
    tasks = [lambda s=symbol: fetch_yahoo(s) for symbol in rest]
    for symbol, result in zip(rest, run_parallel(tasks, workers=6), strict=True):
        if isinstance(result, Exception):
            errors.append(f"yahoo {symbol}: {result}")
        else:
            rows.append(result)
    return rows, errors


def _is_etf_symbol(symbol: str, name: str = "") -> bool:
    blob = f"{symbol} {name}".upper()
    if "ETF" in blob:
        return True
    if symbol[:2] in {"sh", "sz"} and len(symbol) >= 8:
        return symbol[2:4] in {"51", "15", "56", "58"}
    return False


_FALLBACK_BENCHES = {
    "sh000001",
    "sh000300",
    "sz399006",
    "^HSI",
    "^N225",
    "^KS11",
    "^GSPC",
    "^IXIC",
}


def _configured_benchmarks() -> list[dict]:
    return list(load_yaml("sources.yml").get("benchmarks") or [])


def snapshot_from_rows(
    rows: list[QuoteRow],
    source: str,
    *,
    benchmark_ids: list[str] | None = None,
    focus_ids: list[str] | None = None,
    etf_ids: list[str] | None = None,
) -> dict:
    configured = _configured_benchmarks()
    order = [normalize_symbol(row["symbol"]) for row in configured if row.get("symbol")]
    names = {
        normalize_symbol(row["symbol"]): str(row.get("name") or "").strip()
        for row in configured
        if row.get("symbol") and str(row.get("name") or "").strip()
    }
    benches = {
        *order,
        *_FALLBACK_BENCHES,
        *(normalize_symbol(symbol) for symbol in (benchmark_ids or []) if normalize_symbol(symbol)),
    }
    focus = [normalize_symbol(symbol) for symbol in (focus_ids or []) if normalize_symbol(symbol)]
    focus_set = set(focus)
    etf_set = {normalize_symbol(symbol) for symbol in (etf_ids or []) if normalize_symbol(symbol)}
    as_of = ""
    for preferred in ("sh000001", "sh000300", "sz399006"):
        as_of = next((row.asOf for row in rows if row.symbol == preferred and row.asOf), "")
        if as_of:
            break
    if not as_of:
        as_of = next((row.asOf for row in rows if row.asOf), "")
    if focus_set:
        related = [row for row in rows if row.symbol in focus_set]
    else:
        related = [row for row in rows if row.symbol not in benches]
    order_index = {symbol: index for index, symbol in enumerate(order)}

    def dump_row(row: QuoteRow) -> dict:
        data = row.model_dump()
        if names.get(row.symbol):
            data["name"] = names[row.symbol]
        return data

    def is_sector(row: QuoteRow) -> bool:
        if etf_set:
            return row.symbol in etf_set
        return _is_etf_symbol(row.symbol, row.name)

    bench_rows = [row for row in rows if row.symbol in benches]
    bench_rows.sort(key=lambda row: (order_index.get(row.symbol, 999), row.symbol))
    return {
        "asOf": as_of,
        "delayed": True,
        "source": source,
        "benchmarks": [dump_row(row) for row in bench_rows],
        "sectors": [dump_row(row) for row in related if is_sector(row)],
        "tickers": [dump_row(row) for row in related if not is_sector(row)],
    }
