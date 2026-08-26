from __future__ import annotations

from typing import Any

from src.ingest.news import http_client
from src.settings import load_yaml

EASTMONEY_HISTORY = "https://datacenter-web.eastmoney.com/api/data/v1/get"
# 005 = 沪股通 + 深股通（北向合计）
NORTHBOUND_TYPE = "005"


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_northbound_rows(payload: dict, limit: int = 15) -> list[dict[str, Any]]:
    """Parse East Money mutual-connect history. DEAL_AMT is 万元; NET_DEAL_AMT is often null."""
    result = payload.get("result") if isinstance(payload, dict) else None
    data = (result or {}).get("data") or []
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        date = str(item.get("TRADE_DATE") or "")[:10]
        if not date:
            continue
        deal = _num(item.get("DEAL_AMT"))
        net = _num(item.get("NET_DEAL_AMT"))
        rows.append(
            {
                "date": date,
                "dealAmt": deal,
                "dealAmtYi": None if deal is None else round(deal / 10_000.0, 2),
                "netDealAmt": net,
                "indexClose": _num(item.get("INDEX_CLOSE_PRICE")),
                "indexChangePct": _num(item.get("INDEX_CHANGE_RATE")),
                "leadStock": str(item.get("LEAD_STOCKS_NAME") or "").strip(),
                "leadChangePct": _num(item.get("LS_CHANGE_RATE")),
            }
        )
    rows.sort(key=lambda row: row["date"])
    if limit > 0:
        rows = rows[-limit:]
    return rows


def fetch_northbound(limit: int | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    days = limit if limit is not None else int(load_yaml("budgets.yml").get("series_days") or 15)
    params = {
        "reportName": "RPT_MUTUAL_DEAL_HISTORY",
        "columns": "ALL",
        "filter": f'(MUTUAL_TYPE="{NORTHBOUND_TYPE}")',
        "pageNumber": "1",
        "pageSize": str(max(days, 5)),
        "sortTypes": "-1",
        "sortColumns": "TRADE_DATE",
        "source": "WEB",
        "client": "WEB",
    }
    try:
        with http_client(timeout=18.0) as client:
            response = client.get(
                EASTMONEY_HISTORY,
                params=params,
                headers={"Referer": "https://data.eastmoney.com/"},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return [], [f"eastmoney northbound: {exc}"]
    if not isinstance(payload, dict) or not payload.get("success"):
        message = payload.get("message") if isinstance(payload, dict) else "invalid payload"
        return [], [f"eastmoney northbound: {message}"]
    rows = parse_northbound_rows(payload, limit=days)
    if not rows:
        return [], ["eastmoney northbound: empty series"]
    return rows, []
