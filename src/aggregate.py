from __future__ import annotations

from typing import Any

from src.llm import LLMError, chat, model_debug, parse_json_object, resolve_model
from src.models import AnalysisPlan, HeatBoard, HotSearchItem, NewsItem, ThemeCluster
from src.settings import env, load_yaml


def _news_titles(news: list[NewsItem], limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in news[:limit]:
        rows.append(
            {
                "title": item.title,
                "source": item.source,
                "sourceClass": item.sourceClass,
                "highlight": item.highlight,
                "score": item.score,
                "publishedAt": item.publishedAt,
            }
        )
    return rows


def _heat_seed_clusters(heat: HeatBoard | None) -> list[ThemeCluster]:
    if not heat:
        return []
    rows: list[ThemeCluster] = []
    seen: set[str] = set()
    for item in heat.items:
        if item.channel not in {"tape", "web", "social"}:
            continue
        name = (item.name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        words = [token for token in (item.leadName, item.detail) if token]
        if item.channel == "tape":
            summary = (
                f"盘面热点「{name}」"
                + (f"涨跌约 {item.changePct:.2f}%。" if item.changePct is not None else "。")
                + (f"领涨 {item.leadName}。" if item.leadName else "")
                + "来自全市场行业扫描，不依赖本次侧重点。"
            )
        elif item.channel == "web":
            summary = f"网络热搜「{name}」。投资相关性待对照新闻与价格，不能单靠热搜下结论。"
        else:
            summary = f"舆情热点「{name}」。讨论量本身是情绪，映射到板块前需要公告或价格印证。"
        rows.append(
            ThemeCluster(
                name=name[:40],
                summary=summary[:400],
                newsTitles=[],
                hotWords=words[:6],
                heat=round(max(0.0, min(1.0, item.heatScore)), 2),
            )
        )
        if len(rows) >= 6:
            break
    return rows


def heuristic_clusters(
    focus: str,
    plan: AnalysisPlan,
    news: list[NewsItem],
    hot_search: list[HotSearchItem],
    heat: HeatBoard | None = None,
) -> list[ThemeCluster]:
    clusters: list[ThemeCluster] = _heat_seed_clusters(heat)
    used: set[int] = set()
    used_names = {row.name for row in clusters}
    for sector in (plan.sectors or [focus or "综合"])[:6]:
        related = [
            item
            for item in news
            if id(item) not in used and sector and sector in f"{item.title} {item.snippet}"
        ][:8]
        for item in related:
            used.add(id(item))
        if not related and not hot_search:
            continue
        if sector in used_names:
            continue
        used_names.add(sector)
        hot_words = [
            row.word
            for row in hot_search
            if sector[:2] in row.word or any(token and token in row.word for token in plan.keywords[:6])
        ]
        clusters.append(
            ThemeCluster(
                name=sector,
                summary=f"{sector}相关公开报道 {len(related)} 条，热搜 {len(hot_words)} 条。规则聚合，未经模型归类。",
                newsTitles=[item.title for item in related[:6]],
                hotWords=hot_words[:6],
                heat=round(min(1.0, (related[0].score / 12.0) if related else 0.25), 2),
            )
        )
    seen_events: set[str] = set()
    for item in hot_search:
        label = item.cluster or item.word
        if not item.focusEvent or not label or label in seen_events or label in used_names:
            continue
        seen_events.add(label)
        used_names.add(label)
        members = [row.word for row in hot_search if (row.cluster or row.word) == label]
        clusters.insert(
            0,
            ThemeCluster(
                name=label,
                summary=(
                    f"微博上 {item.clusterSize or len(members)} 条热搜指向「{label}」，"
                    f"累计热度 {item.clusterHeat or item.heat or 0}，讨论量权重 {item.attention or 0}。"
                    "大讨论量热点无论原分类是社会、娱乐还是财经，都可能影响风险偏好与相关产业链，必须纳入盘面分析。"
                ),
                newsTitles=[],
                hotWords=members[:8],
                heat=round(
                    min(
                        1.0,
                        max(
                            item.attention or 0.55,
                            (item.clusterHeat or item.heat or 0) / 2_000_000,
                        ),
                    ),
                    2,
                ),
            ),
        )
    if not clusters and (news or hot_search):
        clusters.append(
            ThemeCluster(
                name=focus or "综合",
                summary="未按板块切开，保留综合篮子。",
                newsTitles=[item.title for item in news[:8]],
                hotWords=[row.word for row in hot_search[:8]],
                heat=0.3,
            )
        )
    return clusters[:8]


def _coerce_clusters(raw: Any, news: list[NewsItem]) -> list[ThemeCluster]:
    if not isinstance(raw, list):
        return []
    known = {item.title for item in news}
    rows: list[ThemeCluster] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("sector") or "").strip()
        if not name:
            continue
        titles = item.get("newsTitles") or item.get("titles") or []
        if not isinstance(titles, list):
            titles = [titles]
        clean_titles = [str(title).strip() for title in titles if str(title).strip()]
        matched = [title for title in clean_titles if title in known] or clean_titles[:6]
        words = item.get("hotWords") or item.get("weibo") or []
        if not isinstance(words, list):
            words = [words]
        try:
            heat = float(item.get("heat") or 0)
        except (TypeError, ValueError):
            heat = 0.0
        if heat > 1:
            heat = heat / 10.0 if heat <= 10 else 1.0
        rows.append(
            ThemeCluster(
                name=name[:40],
                summary=str(item.get("summary") or "")[:400],
                newsTitles=matched[:8],
                hotWords=[str(word).strip() for word in words if str(word).strip()][:8],
                heat=max(0.0, min(1.0, heat)),
            )
        )
    return rows[:8]


def aggregate_themes(
    *,
    focus: str,
    plan: AnalysisPlan,
    news: list[NewsItem],
    hot_search: list[HotSearchItem],
    heat: HeatBoard | None = None,
) -> tuple[list[ThemeCluster], str, list[str]]:
    fallback = heuristic_clusters(focus, plan, news, hot_search, heat=heat)
    if not env("AI_API_KEY"):
        return fallback, "heuristic", []
    budgets = load_yaml("budgets.yml")
    news_cap = int(budgets.get("compact_news") or 120)
    prompt = {
        "focus": focus,
        "plan": {
            "sectors": plan.sectors,
            "keywords": plan.keywords,
            "focusKind": plan.focusKind,
        },
        "heat": (heat.model_dump() if heat else {}),
        "news": _news_titles(news, news_cap),
        "hotSearch": [
            {
                "word": item.word,
                "category": item.category,
                "heat": item.heat,
                "match": item.match,
                "cluster": item.cluster,
                "kind": item.kind,
                "focusEvent": item.focusEvent,
                "attention": item.attention,
                "clusterSize": item.clusterSize,
            }
            for item in hot_search[:24]
        ],
        "instructions": (
            "把全市场热点层 heat、新闻与微博热搜聚成 4 到 8 个市场议题。只输出 JSON 对象，字段 themes 为数组。"
            "每项: name, summary(2-4句，点名时效和分歧), newsTitles(必须来自给定 news 的 title),"
            "hotWords(来自 hotSearch.word 或 heat.name，可空), heat(0到1)。"
            "heat.channel=tape 的领涨行业必须单独成题，即使与用户侧重点无关。"
            "同一 cluster 的多条热搜必须合成一个议题；focusEvent=true 或 match=viral 或 attention 高的热点"
            "无论原分类是灾害、明星还是社会新闻，都要单独成题，并写清可能映射到哪些板块。"
            "web 热搜只要够热就要出现，再判断和投资的关系，不要因为不是财经词就丢弃。"
            "讨论量权重（attention/heat）更高的议题优先，heat 字段要体现这个权重。"
            "官方与财联社标红优先进入 summary。热搜只作情绪辅证。"
            "不要编造标题。这不是投资建议。"
        ),
    }
    try:
        model = resolve_model("synthesizer")
        print(f"calling aggregator {model_debug(model)} news={len(news)} hot={len(hot_search)}", flush=True)
        raw = chat(
            [
                {"role": "system", "content": "You cluster financial news and Weibo tape. Return JSON only."},
                {"role": "user", "content": str(prompt)},
            ],
            model=model,
            max_tokens=int(budgets.get("aggregator_max_tokens") or 3500),
            timeout=150.0,
        )
        data = parse_json_object(raw)
        clusters = _coerce_clusters(data.get("themes") or data.get("clusters"), news)
        if not clusters:
            raise LLMError("聚合结果为空")
        print(f"aggregator ok themes={len(clusters)}", flush=True)
        return clusters, model, []
    except (LLMError, ValueError) as exc:
        print(f"aggregator failed: {exc}", flush=True)
        return fallback, "heuristic", [f"议题聚合失败，回退规则分组: {exc}"]
