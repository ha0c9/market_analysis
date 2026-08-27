from __future__ import annotations

from typing import Any

from src.llm import LLMError, chat, model_debug, parse_json_object, resolve_model
from src.models import AnalysisPlan, HotSearchItem, NewsItem, ThemeCluster
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


def heuristic_clusters(
    focus: str,
    plan: AnalysisPlan,
    news: list[NewsItem],
    hot_search: list[HotSearchItem],
) -> list[ThemeCluster]:
    clusters: list[ThemeCluster] = []
    used: set[int] = set()
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
) -> tuple[list[ThemeCluster], str, list[str]]:
    fallback = heuristic_clusters(focus, plan, news, hot_search)
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
        "news": _news_titles(news, news_cap),
        "hotSearch": [
            {"word": item.word, "category": item.category, "heat": item.heat, "match": item.match}
            for item in hot_search[:24]
        ],
        "instructions": (
            "把新闻与微博热搜聚成 4 到 8 个市场议题。只输出 JSON 对象，字段 themes 为数组。"
            "每项: name, summary(2-4句，点名时效和分歧), newsTitles(必须来自给定 news 的 title),"
            "hotWords(来自 hotSearch.word，可空), heat(0到1)。"
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
