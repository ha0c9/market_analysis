from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
REPORTS_DIR = ROOT / "docs" / "reports"
WEB_DIR = ROOT / "web"

USER_AGENT = "market-analysis/0.1 (+https://github.com/ha0c9/market_analysis)"
DEFAULT_BASE_URL = "https://node-hk.sssaiapi.com/api/v1"
