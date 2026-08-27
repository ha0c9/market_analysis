from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from src import REPORTS_DIR, ROOT
from src.distill import distill_news
from src.ingest.cls import fetch_cls_telegraph
from src.ingest.flows import fetch_northbound
from src.ingest.news import fetch_configured_rss, fetch_google_news
from src.ingest.quotes import fetch_quotes, normalize_symbol, snapshot_from_rows
from src.ingest.weibo import fetch_weibo_finance_hot
from src.planner import plan_analysis
from src.pulse import build_market_pulse
from src.settings import load_yaml, write_json
from src.synthesize import synthesize_report
from src.timeutil import isoformat, now_utc, within_lookback


def build_report(focus: str, lookback_hours: int) -> Path:
    budgets = load_yaml("budgets.yml")
    sources = load_yaml("sources.yml")
    plan, planner_model, warnings = plan_analysis(focus, lookback_hours)
    per_source = plan.maxItemsPerSource or int(budgets.get("max_items_per_source") or 20)

    rss_items, rss_errors = fetch_configured_rss(per_source)
    cls_items, cls_errors = fetch_cls_telegraph(per_source)
    gnews_items, gnews_errors = fetch_google_news(plan.newsQueries, per_source)
    hot_items, weibo_errors, weibo_ok = fetch_weibo_finance_hot(
        plan.keywords, plan.lookbackHours
    )
    print(
        f"news rss={len(rss_items)} cls={len(cls_items)} gnews={len(gnews_items)} "
        f"weibo={len(hot_items)} ok={int(weibo_ok)} "
        f"rss_err={len(rss_errors)} cls_err={len(cls_errors)} "
        f"gnews_err={len(gnews_errors)} weibo_err={len(weibo_errors)}",
        flush=True,
    )
    news = distill_news(rss_items + cls_items + gnews_items, plan.keywords, plan.lookbackHours)
    if news and any(not within_lookback(item, plan.lookbackHours) for item in news):
        warnings.append("近期稿件不足，已补充稍早的相关报道")

    quote_symbols = [
        *[row["symbol"] for row in sources.get("benchmarks") or []],
        *[normalize_symbol(symbol) for symbol in plan.etfs],
        *[normalize_symbol(symbol) for symbol in plan.tickers],
    ]
    quotes, quote_errors = fetch_quotes(quote_symbols)
    northbound, flow_errors = fetch_northbound()
    fetch_errors = [
        *rss_errors,
        *cls_errors,
        *gnews_errors,
        *weibo_errors,
        *quote_errors,
        *flow_errors,
    ]
    pulse = build_market_pulse(news, quotes, northbound)
    coverage = {
        "news": bool(news),
        "quotes": bool(quotes),
        "northbound": bool(northbound),
        "filings": False,
        "x": False,
        "weibo": weibo_ok,
    }
    report, model = synthesize_report(
        focus=focus,
        plan=plan,
        news=news,
        quotes=quotes,
        coverage=coverage,
        errors=[*warnings, *fetch_errors],
        market_pulse=pulse,
        hot_search=hot_items,
    )
    if fetch_errors:
        report.limitations.append(f"部分源失败: {len(fetch_errors)}")
    for warning in warnings:
        if warning not in report.limitations:
            report.limitations.append(warning)
    report.stats["plannerModel"] = planner_model
    report.stats["model"] = model
    report.stats["weibo"] = len(hot_items)
    report.marketSnapshot = snapshot_from_rows(
        quotes,
        report.marketSnapshot.get("source") or "tencent+yahoo",
        benchmark_ids=plan.benchmarks,
        focus_ids=[*plan.etfs, *plan.tickers],
        etf_ids=plan.etfs,
    )
    report.marketPulse = pulse

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    public_reports = ROOT / "reports"
    public_reports.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    payload = report.model_dump()
    latest = REPORTS_DIR / "latest.json"
    write_json(latest, payload)
    write_json(REPORTS_DIR / f"{stamp}.json", payload)
    write_json(public_reports / "latest.json", payload)
    sample = REPORTS_DIR / "sample.json"
    if sample.exists():
        shutil.copyfile(sample, public_reports / "sample.json")
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
    parser.add_argument("--focus", default="", help="分析侧重点，如：资金流入分析、尾盘拉升、医药相关、恒瑞医药")
    parser.add_argument("--lookback-hours", type=int, default=36)
    args = parser.parse_args()
    from src.llm import log, probe_llm, public_url_parts, resolve_model
    from src.settings import ai_base_url, env

    log(f"AI_API_KEY={'set' if env('AI_API_KEY') else 'missing'}")
    log(f"AI_BASE_URL {public_url_parts(ai_base_url()) if ai_base_url() else 'empty'}")
    log(f"AI_MODEL_PLANNER={'set' if env('AI_MODEL_PLANNER') else 'empty'}")
    log(f"AI_MODEL_SYNTHESIZER={'set' if env('AI_MODEL_SYNTHESIZER') else 'empty'}")
    log(f"resolved_planner={resolve_model('planner')}")
    log(f"resolved_synthesizer={resolve_model('synthesizer')}")
    if env("AI_API_KEY"):
        probe_llm()
    path = build_report(args.focus.strip(), args.lookback_hours)
    log(f"wrote {path}")


if __name__ == "__main__":
    main()
