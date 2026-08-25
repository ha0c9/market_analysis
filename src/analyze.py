from __future__ import annotations

import argparse
from pathlib import Path

from src import REPORTS_DIR
from src.distill import distill_news
from src.ingest.news import fetch_configured_rss, fetch_google_news
from src.ingest.quotes import fetch_quotes, normalize_symbol, snapshot_from_rows
from src.planner import plan_analysis
from src.settings import load_yaml, write_json
from src.synthesize import synthesize_report
from src.timeutil import isoformat, now_utc, within_lookback


def build_report(focus: str, lookback_hours: int) -> Path:
    budgets = load_yaml("budgets.yml")
    sources = load_yaml("sources.yml")
    plan, planner_model, warnings = plan_analysis(focus, lookback_hours)
    per_source = plan.maxItemsPerSource or int(budgets.get("max_items_per_source") or 20)

    rss_items, rss_errors = fetch_configured_rss(per_source)
    gnews_items, gnews_errors = fetch_google_news(plan.newsQueries, per_source)
    news = distill_news(rss_items + gnews_items, plan.keywords, plan.lookbackHours)
    if news and any(not within_lookback(item, plan.lookbackHours) for item in news):
        warnings.append("近期稿件不足，已补充稍早的相关报道")

    quote_symbols = [
        *[row["symbol"] for row in sources.get("benchmarks") or []],
        *[row["symbol"] for row in sources.get("sector_quotes") or []],
        *[normalize_symbol(symbol) for symbol in plan.tickers],
    ]
    quotes, quote_errors = fetch_quotes(quote_symbols)
    fetch_errors = [*rss_errors, *gnews_errors, *quote_errors]
    coverage = {
        "news": bool(news),
        "quotes": bool(quotes),
        "filings": False,
        "x": False,
        "weibo": False,
    }
    report, model = synthesize_report(
        focus=focus,
        plan=plan,
        news=news,
        quotes=quotes,
        coverage=coverage,
        errors=[*warnings, *fetch_errors],
    )
    if fetch_errors:
        report.limitations.append(f"部分源失败: {len(fetch_errors)}")
    for warning in warnings:
        if warning not in report.limitations:
            report.limitations.append(warning)
    report.stats["plannerModel"] = planner_model
    report.stats["model"] = model
    report.marketSnapshot = snapshot_from_rows(quotes, report.marketSnapshot.get("source") or "tencent+yahoo")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    latest = REPORTS_DIR / "latest.json"
    archive = REPORTS_DIR / f"{stamp}.json"
    write_json(latest, report.model_dump())
    write_json(archive, report.model_dump())
    _refresh_index(REPORTS_DIR, keep=int(budgets.get("report_history") or 30))
    return latest


def _refresh_index(directory: Path, keep: int) -> None:
    files = sorted(
        [path for path in directory.glob("*.json") if path.name not in {"latest.json", "index.json", "sample.json"}],
        reverse=True,
    )
    for stale in files[keep:]:
        stale.unlink(missing_ok=True)
    files = files[:keep]
    write_json(
        directory / "index.json",
        {
            "updatedAt": isoformat(now_utc()),
            "reports": [{"id": path.stem, "file": path.name} for path in files],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a market narrative analysis job")
    parser.add_argument("--focus", default="", help="分析侧重点，如：存储相关")
    parser.add_argument("--lookback-hours", type=int, default=36)
    args = parser.parse_args()
    path = build_report(args.focus.strip(), args.lookback_hours)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
