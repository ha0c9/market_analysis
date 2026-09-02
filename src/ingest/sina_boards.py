from __future__ import annotations

import re
from typing import Any

from src.ingest.news import BROWSER_UA, compact_http_error, http_client
from src.ingest.quotes import normalize_symbol
from src.models import HeatItem
from src.timeutil import isoformat, now_utc

NODES_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodes"
HQ_URL = "https://hq.sinajs.cn/list={codes}"
_NODE = re.compile(r'\["([^"]+)","","(new_[a-z0-9]+)"\]')
_HQ = re.compile(r'hq_str_(bk_new_[a-z0-9]+)="([^"]*)"')

# Live HQNodes listing; used if the directory call fails.
FALLBACK_NODES = [
    "new_blhy", "new_cmyl", "new_cbzz", "new_dlhy", "new_dqhy", "new_dzqj",
    "new_dzxx", "new_fdsb", "new_fzjx", "new_fzhy", "new_fjzz", "new_fzxl",
    "new_gthy", "new_glql", "new_gsgq", "new_hghy", "new_hqhy", "new_hbhy",
    "new_jxhy", "new_jdhy", "new_jjhy", "new_jzjc", "new_jtys", "new_jdly",
    "new_kfq", "new_mthy", "new_mtc", "new_ljhy", "new_nlmy", "new_nyhf",
    "new_qczz", "new_sybh", "new_sphy", "new_snhy", "new_slzp", "new_tchy",
    "new_wzwm", "new_ylqx", "new_yqyb", "new_ysbz", "new_zzhy", "new_syhy",
    "new_zhhy", "new_jrhy", "new_fdc", "new_qtxy", "new_swzz", "new_stock",
]


def parse_sina_hq_body(body: str) -> list[HeatItem]:
    rows: list[HeatItem] = []
    for match in _HQ.finditer(body or ""):
        payload = match.group(2)
        parts = payload.split(",")
        if len(parts) < 9:
            continue
        name = parts[1].strip()
        if not name:
            continue
        try:
            change = float(parts[5])
        except ValueError:
            change = None
        lead_symbol = normalize_symbol(parts[8]) if len(parts) > 8 else ""
        lead_change = None
        if len(parts) > 9:
            try:
                lead_change = float(parts[9])
            except ValueError:
                lead_change = None
        lead_name = parts[12].strip() if len(parts) > 12 else ""
        detail = ""
        if lead_name:
            lead_move = f"{lead_change:+.2f}%" if lead_change is not None else ""
            detail = f"领涨 {lead_name} {lead_move}".strip()
        rows.append(
            HeatItem(
                channel="tape",
                name=name,
                detail=detail,
                changePct=change,
                leadName=lead_name,
                leadSymbol=lead_symbol,
                leadChangePct=lead_change,
            )
        )
    return rows


def _node_ids_from_directory(text: str) -> list[str]:
    return [node for _, node in _NODE.findall(text or "") if node]


def fetch_sina_boards(limit: int = 12) -> tuple[list[HeatItem], list[str]]:
    errors: list[str] = []
    nodes = list(FALLBACK_NODES)
    try:
        with http_client(timeout=18.0, browser=True) as client:
            response = client.get(
                NODES_URL,
                headers={"User-Agent": BROWSER_UA, "Referer": "https://finance.sina.com.cn/"},
            )
            response.raise_for_status()
            found = _node_ids_from_directory(response.text)
            if found:
                nodes = found
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sina boards directory: {compact_http_error(exc)}")
    codes = ",".join(f"bk_{node}" for node in nodes)
    try:
        with http_client(timeout=18.0, browser=True) as client:
            response = client.get(
                HQ_URL.format(codes=codes),
                headers={"User-Agent": BROWSER_UA, "Referer": "https://finance.sina.com.cn/"},
            )
            response.raise_for_status()
            rows = parse_sina_hq_body(response.text)
    except Exception as exc:  # noqa: BLE001
        return [], [*errors, f"sina boards: {compact_http_error(exc)}"]
    if not rows:
        return [], [*errors, "sina boards: empty"]
    gainers = sorted(
        [row for row in rows if (row.changePct or 0) > 0],
        key=lambda row: float(row.changePct or 0),
        reverse=True,
    )
    losers = sorted(
        [row for row in rows if (row.changePct or 0) < 0],
        key=lambda row: float(row.changePct or 0),
    )
    ranked = [*gainers[: max(8, limit - 4)], *losers[:4]]
    if not ranked:
        ranked = sorted(rows, key=lambda row: abs(float(row.changePct or 0)), reverse=True)[:limit]
    peak = max((abs(float(row.changePct or 0)) for row in ranked), default=1.0) or 1.0
    now = isoformat(now_utc())
    kept: list[HeatItem] = []
    for index, row in enumerate(ranked[:limit], start=1):
        score = min(1.0, abs(float(row.changePct or 0)) / max(peak, 1.0))
        kept.append(row.model_copy(update={"rank": index, "heatScore": score, "asOf": now}))
    return kept, errors
