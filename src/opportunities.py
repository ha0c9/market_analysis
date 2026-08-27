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


def _hotspot_label(items: list[HotSearchItem]) -> str:
    focus = [it for it in items if it.focusEvent]
    pool = focus or items
    if not pool:
        return ""
    best = max(pool, key=lambda it: (it.clusterHeat or 0, it.heat or 0))
    return best.cluster or best.word


def heuristic_opportunities(
    hot_search: list[HotSearchItem],
    aggregates: list[ThemeCluster] | None = None,
    limit: int = 8,
) -> list[Opportunity]:
    if not hot_search:
        return []
    rows: list[Opportunity] = []
    seen: set[str] = set()
    for spec in _maps():
        tokens = [str(token) for token in (spec.get("match") or []) if str(token).strip()]
        hits = [
            item
            for item in hot_search
            if any(token in _cluster_blob(item) for token in tokens)
        ]
        if not hits:
            continue
        hotspot = _hotspot_label(hits)
        members = sorted({item.word for item in hits if item.cluster == hotspot or hotspot in (item.cluster, item.word)})
        heat = max((item.clusterHeat or item.heat or 0) for item in hits)
        confidence = 0.42 if any(item.focusEvent for item in hits) else 0.32
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
            thesis = (
                f"由热搜「{hotspot}」联想到{name}。"
                + (f"{angle}。" if angle else "")
                + (
                    f"同类条目包括 {sample}。"
                    if sample
                    else ""
                )
                + "过往重大灾害/产业事件后市场常交易相关产业链预期，这里只作对照线索，需用公告与价格验证。"
            )
            rows.append(
                Opportunity(
                    symbol=symbol,
                    name=name,
                    hotspot=hotspot,
                    thesis=thesis[:400],
                    angle=angle,
                    confidence=confidence if heat else 0.28,
                )
            )
            if len(rows) >= limit:
                return rows
    if not rows and aggregates:
        for cluster in aggregates[:2]:
            name = cluster.name.strip()
            if not name:
                continue
            rows.append(
                Opportunity(
                    symbol="",
                    name="",
                    hotspot=name,
                    thesis=f"议题「{name}」尚未映射到具体上市公司，仅作关注线索。",
                    angle="",
                    confidence=0.2,
                )
            )
            break
    return [row for row in rows if row.symbol][:limit]


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
) -> list[Opportunity]:
    if not isinstance(raw, list):
        return fallback
    known = _ticker_index()
    clusters = [item.cluster or item.word for item in hot_search if item.cluster or item.word]
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
        if hotspot and clusters and hotspot not in clusters and not any(hotspot in c or c in hotspot for c in clusters):
            hotspot = clusters[0]
        if not hotspot:
            hotspot = (mapped and "") or (clusters[0] if clusters else "")
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
    fallback = heuristic_opportunities(hot_search, aggregates, limit=limit)
    if not env("AI_API_KEY") or not hot_search:
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
            "重大灾害优先想基建/水泥/水利/保险；流量明星/影视热搜优先想传媒、广告、代言相关消费。"
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
        )
        if not rows:
            raise LLMError("个股推演为空")
        print(f"opportunities ok n={len(rows)}", flush=True)
        return rows, model, []
    except (LLMError, ValueError) as exc:
        print(f"opportunities failed: {exc}", flush=True)
        return fallback, "heuristic", [f"个股推演失败，回退热点映射: {exc}"]
