from __future__ import annotations

import re
from statistics import mean
from typing import Any

from src.llm import LLMError, chat, model_debug, parse_json_object, resolve_model
from src.models import AnalysisPlan, Evidence, NewsItem, QuoteRow, Report, SectorOutlook
from src.settings import env, load_yaml
from src.timeutil import isoformat, now_utc, parse_datetime

_HEAT_RANK = {
    "high": 1,
    "hot": 1,
    "strong": 1,
    "高": 1,
    "高热": 1,
    "强": 1,
    "medium": 2,
    "mid": 2,
    "moderate": 2,
    "中": 2,
    "中等": 2,
    "low": 3,
    "weak": 3,
    "低": 3,
    "弱": 3,
}
_PRICE_ACTIONS = {"up", "down", "mixed", "flat", "unknown"}
_DIRECTIONS = {"up", "down", "mixed", "unclear"}
_CALIBRATIONS = {"confirming", "pricedIn", "divergence", "insufficientData"}
_ISO_TAIL = re.compile(r"\((20\d{2}-\d{2}-\d{2}T[^)]+)\)\s*$")


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


def _match_news(text: str, news: list[NewsItem]) -> NewsItem | None:
    needle = (text or "").strip()
    if not needle:
        return None
    for item in news:
        if needle[:24] and (needle[:24] in item.title or item.title[:24] in needle):
            return item
        if item.url and item.url in needle:
            return item
    return None


def _coerce_heat(value: Any, index: int) -> int:
    if isinstance(value, bool):
        return index
    if isinstance(value, (int, float)):
        number = int(value)
        return number if number >= 1 else index
    text = str(value or "").strip().lower()
    if text in _HEAT_RANK:
        return _HEAT_RANK[text]
    if text.isdigit():
        return max(1, int(text))
    return index


def _coerce_heat_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 1.0:
        score = score / 10.0 if score <= 10 else 1.0
    return max(0.0, min(1.0, score))


def _coerce_price_action(value: Any) -> tuple[str, str]:
    if isinstance(value, str) and value.strip().lower() in _PRICE_ACTIONS:
        return value.strip().lower(), ""
    text = str(value or "").strip()
    if not text:
        return "unknown", ""
    has_down = any(token in text for token in ("下跌", "走弱", "偏空", "大跌"))
    has_up = any(token in text for token in ("上涨", "走强", "偏多", "大涨"))
    if has_up and has_down:
        return "mixed", text
    if has_down:
        return "down", text
    if has_up:
        return "up", text
    if any(token in text for token in ("无直接", "不足", "缺乏", "unknown")):
        return "unknown", text
    lower = text.lower()
    if lower in _PRICE_ACTIONS:
        return lower, ""
    return "unknown", text


def _coerce_direction(value: Any, price_action: str) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "neutral": "unclear",
        "flat": "unclear",
        "中性": "unclear",
        "不明": "unclear",
        "偏多": "up",
        "偏空": "down",
        "混杂": "mixed",
    }
    if text in mapping:
        return mapping[text]
    if text in _DIRECTIONS:
        return text
    if price_action in {"up", "down", "mixed"}:
        return "unclear" if price_action == "mixed" else price_action
    return "unclear"


def _coerce_calibration(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {
        "价讯共振": "confirming",
        "或已计价": "pricedIn",
        "价讯背离": "divergence",
        "行情不足": "insufficientData",
        "priced_in": "pricedIn",
        "insufficient": "insufficientData",
        "insufficient_data": "insufficientData",
    }
    if text in mapping:
        return mapping[text]
    if text in _CALIBRATIONS:
        return text
    return "insufficientData"


def _coerce_evidence_item(item: Any, news: list[NewsItem]) -> dict[str, Any] | None:
    if isinstance(item, Evidence):
        return item.model_dump()
    if isinstance(item, dict):
        title = str(item.get("sourceTitle") or item.get("title") or item.get("claim") or "").strip()
        claim = str(item.get("claim") or title).strip()
        url = str(item.get("url") or "").strip()
        published = str(item.get("publishedAt") or "").strip()
        matched = _match_news(title or claim, news)
        if matched:
            url = url or matched.url
            published = published or matched.publishedAt
            title = title or matched.title
        if not claim:
            return None
        weight = item.get("weight")
        return {
            "claim": claim[:300],
            "sourceTitle": (title or claim)[:200],
            "url": url,
            "publishedAt": published,
            "weight": weight if weight in {"primary", "supporting"} else "supporting",
        }
    if isinstance(item, str) and item.strip():
        text = item.strip()
        published = ""
        match = _ISO_TAIL.search(text)
        if match:
            published = match.group(1)
            text = text[: match.start()].strip()
        matched = _match_news(text, news)
        return {
            "claim": text[:300],
            "sourceTitle": (matched.title if matched else text)[:200],
            "url": matched.url if matched else "",
            "publishedAt": published or (matched.publishedAt if matched else ""),
            "weight": "supporting",
        }
    return None


def _coerce_evidence_list(value: Any, news: list[NewsItem]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str) and value.strip() and not value.strip().startswith("无"):
        items = [value.strip()]
    else:
        return []
    rows = []
    for item in items:
        coerced = _coerce_evidence_item(item, news)
        if coerced:
            rows.append(coerced)
    return rows


def normalize_sector_outlook(
    raw_items: list[Any],
    news: list[NewsItem],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items or [], start=1):
        if not isinstance(raw, dict):
            continue
        price_action, leftover = _coerce_price_action(raw.get("priceAction"))
        narrative = str(raw.get("narrative") or "").strip()
        if leftover and leftover not in narrative:
            narrative = f"{narrative} {leftover}".strip() if narrative else leftover
        row = {
            "sector": str(raw.get("sector") or f"板块{index}"),
            "heat": _coerce_heat(raw.get("heat"), index),
            "heatScore": _coerce_heat_score(raw.get("heatScore")),
            "priceAction": price_action,
            "calibration": _coerce_calibration(raw.get("calibration")),
            "direction": _coerce_direction(raw.get("direction"), price_action),
            "narrative": narrative or "模型未给出完整叙述。",
            "evidence": _coerce_evidence_list(raw.get("evidence"), news),
            "counterEvidence": _coerce_evidence_list(raw.get("counterEvidence"), news),
            "confidence": _coerce_heat_score(raw.get("confidence")) or 0.4,
            "invalidatedIf": str(raw.get("invalidatedIf") or "").strip(),
        }
        rows.append(SectorOutlook.model_validate(row).model_dump())
    return rows


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
        marketSnapshot=snapshot_from_rows(
            quotes,
            "tencent+yahoo",
            benchmark_ids=plan.benchmarks,
            focus_ids=[*plan.etfs, *plan.tickers],
            etf_ids=plan.etfs,
        ),
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
            "根据新闻与行情快照写市场研究摘要。只输出 JSON 对象。"
            "sectorOutlook 为数组，每项字段类型必须严格如下："
            "sector=字符串;"
            "heat=整数排名1/2/3，不要写 high/medium/low;"
            "heatScore=0到1的小数;"
            "priceAction 只能是 up|down|mixed|flat|unknown，不要写句子;"
            "calibration 只能是 confirming|pricedIn|divergence|insufficientData;"
            "direction 只能是 up|down|mixed|unclear，不要写 neutral;"
            "narrative=字符串;"
            "evidence 和 counterEvidence 必须是对象数组，每项含 claim,sourceTitle,url,publishedAt,weight;"
            "weight 只能是 primary 或 supporting;"
            "confidence=0到1小数; invalidatedIf=字符串。"
            "另需 crossSectorNotes 字符串。"
            "每条前瞻必须提到价格是否已反应；没有行情则 calibration=insufficientData。"
            "evidence 必须来自给定 news 的 title/url/publishedAt，禁止编造链接。"
            "这不是投资建议，不要给买卖点或目标价。"
        ),
    }
    try:
        model = resolve_model("synthesizer")
        print(f"calling synthesizer {model_debug(model)}", flush=True)
        raw = chat(
            [
                {"role": "system", "content": "You are a financial research assistant. Return JSON only."},
                {"role": "user", "content": str(prompt)},
            ],
            model=model,
            max_tokens=int(load_yaml("budgets.yml").get("synthesizer_max_tokens") or 2200),
            timeout=180.0,
        )
        data = parse_json_object(raw)
        fallback_dump = fallback.model_dump()
        if isinstance(data.get("sectorOutlook"), list) and data["sectorOutlook"]:
            normalized = normalize_sector_outlook(data["sectorOutlook"], news)
            if normalized:
                fallback_dump["sectorOutlook"] = normalized
            else:
                raise LLMError("模型前瞻字段无法规范化")
        if isinstance(data.get("crossSectorNotes"), str) and data["crossSectorNotes"].strip():
            fallback_dump["crossSectorNotes"] = data["crossSectorNotes"]
        fallback_dump["stats"]["model"] = model
        report = Report.model_validate(fallback_dump)
        print(f"synthesizer ok outlook={len(report.sectorOutlook)}", flush=True)
        return report, model
    except (LLMError, ValueError) as exc:
        print(f"synthesizer failed: {exc}", flush=True)
        fallback.errors.append(f"综合模型失败，保留规则草稿: {exc}")
        fallback.limitations.append("大模型综合失败")
        return fallback, "heuristic"
