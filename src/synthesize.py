from __future__ import annotations

from statistics import mean

from src.llm import LLMError, chat, parse_json_object, resolve_model
from src.models import AnalysisPlan, Evidence, NewsItem, QuoteRow, Report, SectorOutlook
from src.settings import env, load_yaml
from src.timeutil import isoformat, now_utc, parse_datetime


def _avg_change(rows: list[QuoteRow]) -> float | None:
    values = [row.changePct for row in rows if row.changePct is not None]
    if not values:
        return None
    return mean(values)


def _price_action(change: float | None) -> str:
    if change is None:
        return "unknown"
    if change >= 1.0:
        return "up"
    if change <= -1.0:
        return "down"
    if abs(change) < 0.3:
        return "flat"
    return "mixed"


def _calibrate(heat_score: float, change: float | None) -> str:
    if change is None:
        return "insufficientData"
    if heat_score >= 0.55 and change >= 3.0:
        return "pricedIn"
    if heat_score >= 0.4 and change <= -1.0:
        return "divergence"
    if heat_score >= 0.4 and change >= 0.4:
        return "confirming"
    if heat_score >= 0.4 and abs(change) < 0.4:
        return "divergence"
    return "confirming"


def _related_quotes(sector: str, keywords: list[str], quotes: list[QuoteRow]) -> list[QuoteRow]:
    tokens = [sector.lower(), *[keyword.lower() for keyword in keywords]]
    related = []
    for row in quotes:
        blob = f"{row.symbol} {row.name}".lower()
        if any(token and token in blob for token in tokens):
            related.append(row)
    return related or quotes


def _evidence_from_news(items: list[NewsItem], limit: int = 3) -> list[Evidence]:
    rows: list[Evidence] = []
    for index, item in enumerate(items[:limit]):
        rows.append(
            Evidence(
                claim=item.snippet[:180] or item.title,
                sourceTitle=item.title,
                url=item.url,
                publishedAt=item.publishedAt,
                weight="primary" if index == 0 else "supporting",
            )
        )
    return rows


def heuristic_report(
    *,
    focus: str,
    plan: AnalysisPlan,
    news: list[NewsItem],
    quotes: list[QuoteRow],
    coverage: dict[str, bool],
    errors: list[str],
    model: str,
) -> Report:
    now = now_utc()
    outlook: list[SectorOutlook] = []
    for index, sector in enumerate(plan.sectors or ["综合"], start=1):
        related_news = [
            item
            for item in news
            if sector[:2] in item.title or any(k.lower() in (item.title + item.snippet).lower() for k in plan.keywords[:6])
        ] or news[:8]
        related_quotes = _related_quotes(sector, plan.keywords, quotes)
        change = _avg_change(related_quotes)
        heat_score = min(1.0, (related_news[0].score / 12.0) if related_news else 0.2)
        outlook.append(
            SectorOutlook(
                sector=sector,
                heat=index,
                heatScore=round(heat_score, 2),
                priceAction=_price_action(change),  # type: ignore[arg-type]
                calibration=_calibrate(heat_score, change),  # type: ignore[arg-type]
                direction="up" if (change or 0) > 0.5 else "down" if (change or 0) < -0.5 else "unclear",
                narrative=(
                    f"{sector}近期相关报道 {len(related_news)} 条。"
                    + (
                        f"相关标的均涨跌 {change:.2f}%。"
                        if change is not None
                        else "未取到足够行情，仅依据公开新闻。"
                    )
                    + "此段为规则草稿，尚未经大模型综合。"
                ),
                evidence=_evidence_from_news(related_news),
                confidence=0.35 if change is None else 0.45,
                invalidatedIf="出现与当前报道方向相反的官方政策或财报，或相关标的大幅反向波动。",
            )
        )
    from src.ingest.quotes import snapshot_from_rows

    start = now
    if news:
        parsed = [parse_datetime(item.publishedAt) for item in news]
        parsed_ok = [dt for dt in parsed if dt]
        if parsed_ok:
            start = min(parsed_ok)
    return Report(
        generatedAt=isoformat(now),
        focus=focus,
        timeWindow={"from": isoformat(start), "to": isoformat(now)},
        dataCoverage=coverage,
        marketSnapshot=snapshot_from_rows(quotes, "tencent+yahoo"),
        sectorOutlook=outlook,
        crossSectorNotes="规则模式仅做分组摘要；配置 AI_API_KEY 后由模型对照价格写前瞻。",
        limitations=["未接入微博/X", "可能使用延迟公开行情"],
        stats={
            "fetched": len(news),
            "used": min(len(news), 60),
            "quotes": len(quotes),
            "model": model,
            "estCostUsd": 0,
        },
        errors=errors,
    )


def synthesize_report(
    *,
    focus: str,
    plan: AnalysisPlan,
    news: list[NewsItem],
    quotes: list[QuoteRow],
    coverage: dict[str, bool],
    errors: list[str],
) -> tuple[Report, str]:
    fallback = heuristic_report(
        focus=focus,
        plan=plan,
        news=news,
        quotes=quotes,
        coverage=coverage,
        errors=errors,
        model="heuristic",
    )
    if not env("AI_API_KEY"):
        fallback.limitations.append("未调用大模型")
        return fallback, "heuristic"
    compact_news = [
        {
            "title": item.title,
            "source": item.source,
            "url": item.url,
            "publishedAt": item.publishedAt,
            "snippet": item.snippet[:280],
            "score": item.score,
        }
        for item in news[:50]
    ]
    compact_quotes = [row.model_dump() for row in quotes]
    prompt = {
        "focus": focus,
        "plan": plan.model_dump(),
        "news": compact_news,
        "quotes": compact_quotes,
        "instructions": (
            "根据新闻与行情快照写市场研究摘要。必须输出 JSON 对象，字段:"
            "sectorOutlook(数组, 每项含 sector,heat,heatScore,priceAction,"
            "calibration,direction,narrative,evidence,counterEvidence,confidence,invalidatedIf),"
            "crossSectorNotes。"
            "calibration 只能是 confirming|pricedIn|divergence|insufficientData。"
            "每条前瞻必须提到价格是否已反应；没有行情则 calibration=insufficientData。"
            "evidence 必须来自给定 news 的 title/url/publishedAt，禁止编造链接。"
            "这不是投资建议，不要给买卖点或目标价。"
        ),
    }
    try:
        model = resolve_model("synthesizer")
        print(f"calling synthesizer model={model}")
        raw = chat(
            [
                {"role": "system", "content": "You are a financial research assistant. Return JSON only."},
                {"role": "user", "content": str(prompt)},
            ],
            model=model,
            max_tokens=int(load_yaml("budgets.yml").get("synthesizer_max_tokens") or 2200),
        )
        data = parse_json_object(raw)
        fallback_dump = fallback.model_dump()
        if isinstance(data.get("sectorOutlook"), list) and data["sectorOutlook"]:
            fallback_dump["sectorOutlook"] = data["sectorOutlook"]
        if isinstance(data.get("crossSectorNotes"), str) and data["crossSectorNotes"].strip():
            fallback_dump["crossSectorNotes"] = data["crossSectorNotes"]
        fallback_dump["stats"]["model"] = model
        report = Report.model_validate(fallback_dump)
        return report, model
    except (LLMError, ValueError) as exc:
        fallback.errors.append(f"综合模型失败，保留规则草稿: {exc}")
        fallback.limitations.append("大模型综合失败")
        return fallback, "heuristic"
