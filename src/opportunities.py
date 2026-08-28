from __future__ import annotations

from typing import Any

from src.ingest.quotes import normalize_symbol
from src.llm import LLMError, chat, model_debug, parse_json_object, resolve_model
from src.models import HotSearchItem, NewsItem, Opportunity, QuoteRow, ThemeCluster
from src.settings import env, load_yaml


def _maps() -> list[dict[str, Any]]:
    data = load_yaml("hotspot_maps.yml")
    rows = data.get("maps") or []
    return [row for row in rows if isinstance(row, dict)]


def _cluster_blob(item: HotSearchItem) -> str:
    return f"{item.cluster or ''} {item.word} {item.kind or ''} {item.category or ''}"


def _opportunity_corpus(
    hot_search: list[HotSearchItem],
    news: list[NewsItem] | None,
    aggregates: list[ThemeCluster] | None,
    focus: str,
) -> str:
    chunks = [focus or ""]
    for item in hot_search:
        chunks.append(_cluster_blob(item))
        chunks.append(item.kind or "")
    for item in news or []:
        chunks.append(f"{item.title} {item.snippet}")
    for row in aggregates or []:
        chunks.append(f"{row.name} {row.summary} {' '.join(row.hotWords)}")
    return " ".join(chunks)


def _hotspot_label(items: list[HotSearchItem]) -> str:
    focus = [it for it in items if it.focusEvent]
    pool = focus or items
    if not pool:
        return ""
    best = max(pool, key=lambda it: (it.clusterHeat or 0, it.heat or 0))
    return best.cluster or best.word


def _spec_matches(spec: dict[str, Any], corpus: str, hot_search: list[HotSearchItem]) -> bool:
    tokens = [str(token) for token in (spec.get("match") or []) if str(token).strip()]
    if not tokens or not any(token in corpus for token in tokens):
        return False
    required = [str(token) for token in (spec.get("require_any") or []) if str(token).strip()]
    if not required:
        return True
    kinds = {item.kind for item in hot_search}
    return any(token in corpus or token in kinds for token in required)


def _hotspot_for_spec(
    spec: dict[str, Any],
    hits: list[HotSearchItem],
    aggregates: list[ThemeCluster] | None,
    focus: str,
) -> str:
    if hits:
        return _hotspot_label(hits)
    tokens = [str(token) for token in (spec.get("match") or []) if str(token).strip()]
    extra = [str(token) for token in (spec.get("require_any") or []) if str(token).strip()]
    for row in aggregates or []:
        blob = f"{row.name} {row.summary}"
        if any(token in blob for token in [*tokens, *extra]):
            return row.name[:40]
    return (focus or "").strip()[:40]


def heuristic_opportunities(
    hot_search: list[HotSearchItem],
    aggregates: list[ThemeCluster] | None = None,
    news: list[NewsItem] | None = None,
    focus: str = "",
    limit: int = 8,
) -> list[Opportunity]:
    corpus = _opportunity_corpus(hot_search, news, aggregates, focus)
    if not corpus.strip():
        return []
    ranked: list[tuple[float, int, Opportunity]] = []
    seen: set[str] = set()
    kinds = {item.kind for item in hot_search}
    social_context = "social" in kinds or any(
        token in corpus for token in ("艺人", "明星", "景甜")
    )
    for spec_index, spec in enumerate(_maps()):
        if not _spec_matches(spec, corpus, hot_search):
            continue
        tokens = [str(token) for token in (spec.get("match") or []) if str(token).strip()]
        required = [str(token) for token in (spec.get("require_any") or []) if str(token).strip()]
        hits = [
            item
            for item in hot_search
            if any(token in _cluster_blob(item) for token in [*tokens, *required])
        ]
        hotspot = _hotspot_for_spec(spec, hits, aggregates, focus)
        if not hotspot:
            continue
        meme = str(spec.get("style") or "") == "meme"
        members = sorted({item.word for item in hits if item.word})
        heat = max((item.clusterHeat or item.heat or 0) for item in hits) if hits else 400_000
        confidence = 0.32 if meme else (0.42 if any(item.focusEvent for item in hits) else 0.32)
        score = float(heat)
        if meme and social_context:
            score += 1_500_000
        for ticker in spec.get("tickers") or []:
            if not isinstance(ticker, dict):
                continue
            symbol = normalize_symbol(str(ticker.get("symbol") or ""))
            name = str(ticker.get("name") or symbol).strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            angle = str(ticker.get("angle") or "").strip()
            sample = "、".join(list(members)[:4])
            if meme:
                thesis = (
                    f"由热点「{hotspot}」联想到{name}。"
                    + (f"{angle}。" if angle else "")
                    + (f"相关条目：{sample}。" if sample else "")
                    + "长文或热搜里的产品细节（磨指甲、修甲）会被交易成对应消费品，不是代言或订单；"
                    "情绪脉冲开盘后常见冲高回落，需用价格验证，不能当成基本面。"
                )
            else:
                thesis = (
                    f"由热搜「{hotspot}」联想到{name}。"
                    + (f"{angle}。" if angle else "")
                    + (f"同类条目包括 {sample}。" if sample else "")
                    + "过往类似事件后市场常交易相关产业链预期，这里只作对照线索，需用公告与价格验证。"
                )
            ranked.append(
                (
                    score,
                    spec_index,
                    Opportunity(
                        symbol=symbol,
                        name=name,
                        hotspot=hotspot,
                        thesis=thesis[:400],
                        angle=angle,
                        confidence=confidence,
                    ),
                )
            )
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [item for _, _, item in ranked][:limit]


def _ticker_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for spec in _maps():
        for ticker in spec.get("tickers") or []:
            if not isinstance(ticker, dict):
                continue
            symbol = normalize_symbol(str(ticker.get("symbol") or ""))
            name = str(ticker.get("name") or "").strip()
            if not symbol:
                continue
            index[symbol] = {
                "symbol": symbol,
                "name": name,
                "angle": str(ticker.get("angle") or "").strip(),
            }
            if name:
                index[name] = index[symbol]
    return index


def _looks_listed(symbol: str) -> bool:
    text = normalize_symbol(symbol)
    return bool(text[:2] in {"sh", "sz"} and text[2:].isdigit() and len(text) == 8)


def coerce_opportunities(
    raw: Any,
    hot_search: list[HotSearchItem],
    fallback: list[Opportunity],
    limit: int = 8,
    focus: str = "",
) -> list[Opportunity]:
    if not isinstance(raw, list):
        return fallback
    known = _ticker_index()
    clusters = [item.cluster or item.word for item in hot_search if item.cluster or item.word]
    fallback_by_symbol = {row.symbol: row for row in fallback}
    rows: list[Opportunity] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        symbol = normalize_symbol(str(item.get("symbol") or ""))
        mapped = known.get(symbol) or known.get(name)
        if mapped:
            symbol = mapped["symbol"]
            name = name or mapped["name"]
        if not symbol or not _looks_listed(symbol) or symbol in seen:
            continue
        if not name:
            name = mapped["name"] if mapped else symbol
        hotspot = str(item.get("hotspot") or item.get("cluster") or "").strip()
        if not hotspot and symbol in fallback_by_symbol:
            hotspot = fallback_by_symbol[symbol].hotspot
        if not hotspot:
            hotspot = (focus or "").strip() or (clusters[0] if clusters else "")
        if not hotspot:
            continue
        try:
            confidence = float(item.get("confidence") or 0.3)
        except (TypeError, ValueError):
            confidence = 0.3
        if confidence > 1:
            confidence = confidence / 10.0 if confidence <= 10 else 1.0
        thesis = str(item.get("thesis") or item.get("why") or "").strip()
        angle = str(item.get("angle") or (mapped["angle"] if mapped else "")).strip()
        if not thesis:
            thesis = f"由热搜「{hotspot}」联想到{name}。" + (f"{angle}。" if angle else "")
        seen.add(symbol)
        rows.append(
            Opportunity(
                symbol=symbol,
                name=name[:40],
                hotspot=hotspot[:40],
                thesis=thesis[:400],
                angle=angle[:80],
                confidence=max(0.0, min(1.0, confidence)),
            )
        )
        if len(rows) >= limit:
            break
    return rows or fallback


def merge_opportunities(
    llm_rows: list[Opportunity],
    fallback: list[Opportunity],
    limit: int = 8,
) -> list[Opportunity]:
    """Keep model order, then fill gaps from the heuristic basket (e.g. meme names)."""
    seen: set[str] = set()
    merged: list[Opportunity] = []
    for row in [*llm_rows, *fallback]:
        if not row.symbol or row.symbol in seen:
            continue
        seen.add(row.symbol)
        merged.append(row)
        if len(merged) >= limit:
            break
    return merged


def opportunity_news_queries(
    focus: str,
    hot_search: list[HotSearchItem],
    aggregates: list[ThemeCluster] | None = None,
    limit: int = 3,
) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        text = (query or "").strip()
        if text and text not in seen:
            seen.add(text)
            queries.append(text)

    if focus.strip():
        add(f"{focus.strip()} 概念股")
    for item in hot_search:
        if item.focusEvent and (item.kind == "social" or item.match == "viral"):
            add(f"{item.cluster or item.word} 磨指甲 指甲刀 概念股")
            break
    for row in aggregates or []:
        blob = f"{row.name} {row.summary}"
        if any(token in blob for token in ("指甲", "小作文", "景甜", "孙宇晨")):
            add(f"{row.name[:24]} 概念股")
            break
    return queries[:limit]


def attach_quotes(rows: list[Opportunity], quotes: list[QuoteRow]) -> list[Opportunity]:
    by_symbol = {row.symbol: row for row in quotes}
    attached: list[Opportunity] = []
    for item in rows:
        quote = by_symbol.get(item.symbol)
        if not quote:
            attached.append(item)
            continue
        attached.append(
            item.model_copy(
                update={
                    "name": item.name or quote.name,
                    "price": quote.price,
                    "changePct": quote.changePct,
                    "asOf": quote.asOf,
                }
            )
        )
    return attached


def infer_opportunities(
    *,
    focus: str,
    hot_search: list[HotSearchItem],
    news: list[NewsItem],
    aggregates: list[ThemeCluster] | None = None,
    limit: int = 8,
) -> tuple[list[Opportunity], str, list[str]]:
    fallback = heuristic_opportunities(
        hot_search, aggregates, news=news, focus=focus, limit=limit
    )
    has_signal = bool(hot_search or news or (focus or "").strip())
    if not env("AI_API_KEY") or not has_signal:
        return fallback, "heuristic", []
    budgets = load_yaml("budgets.yml")
    prompt = {
        "focus": focus,
        "hotSearch": [
            {
                "word": item.word,
                "cluster": item.cluster,
                "kind": item.kind,
                "focusEvent": item.focusEvent,
                "attention": item.attention,
                "heat": item.heat,
                "clusterHeat": item.clusterHeat,
                "clusterSize": item.clusterSize,
            }
            for item in hot_search[:24]
        ],
        "aggregates": [{"name": row.name, "summary": row.summary} for row in (aggregates or [])[:8]],
        "newsTitles": [item.title for item in news[:40]],
        "maps": _maps(),
        "instructions": (
            "根据微博热点（尤其 focusEvent=true 或 attention 高的大讨论量热点）对照过往类似事件，推演可能被交易的 A 股线索。"
            "只输出 JSON 对象，字段 opportunities 为数组，最多 8 条。"
            "每项: symbol(sh/sz+6位), name, hotspot(必须来自 hotSearch.cluster 或 word), "
            "thesis(点明由哪个热点联想到这只股票、历史类似事件怎么交易过、当前还缺什么验证), "
            "angle 短标签, confidence 0到1。"
            "优先使用 maps 里的标的；可以补充其他 A 股，但必须能说清和热点的因果，禁止美股代码。"
            "重大灾害优先想基建/水泥/水利/保险。"
            "流量明星/影视热搜不要只映射传媒：先读 newsTitles 里的故事细节。"
            "长文如果写到磨指甲、剪指甲、修甲，A股会交易指甲刀/指甲剪（张小泉），因为公司有该类产品，不是谐音，也不是代言。"
            "没有指甲/修甲细节就不要强行映射刀剪股。"
            "这类线索必须写明是情绪炒作、与基本面无关、常见冲高回落。"
            "也要读 newsTitles，不要只看热搜词。"
            "不要因为原分类是娱乐就放弃推演。"
            "这不是投资建议，不要写买入/卖出/目标价/点位。"
        ),
    }
    try:
        model = resolve_model("synthesizer")
        print(f"calling opportunities {model_debug(model)} hot={len(hot_search)}", flush=True)
        raw = chat(
            [
                {
                    "role": "system",
                    "content": "You map social hotspots to listed-stock research clues. Return JSON only. No buy/sell advice.",
                },
                {"role": "user", "content": str(prompt)},
            ],
            model=model,
            max_tokens=int(budgets.get("opportunities_max_tokens") or 2200),
            timeout=90.0,
        )
        data = parse_json_object(raw)
        rows = coerce_opportunities(
            data.get("opportunities") or data.get("tickers"),
            hot_search,
            fallback,
            limit=limit,
            focus=focus,
        )
        rows = merge_opportunities(rows, fallback, limit=limit)
        if not rows:
            raise LLMError("个股推演为空")
        print(f"opportunities ok n={len(rows)}", flush=True)
        return rows, model, []
    except (LLMError, ValueError) as exc:
        print(f"opportunities failed: {exc}", flush=True)
        return fallback, "heuristic", [f"个股推演失败，回退热点映射: {exc}"]
