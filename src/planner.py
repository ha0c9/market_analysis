from __future__ import annotations

from src.llm import LLMError, chat, parse_json_object, pick_model
from src.models import AnalysisPlan
from src.settings import env, load_yaml


def _match_preset(focus: str) -> dict:
    data = load_yaml("presets.yml")
    lowered = focus.lower()
    for row in data.get("presets") or []:
        for token in row.get("match") or []:
            if token.lower() in lowered:
                return row
    return data.get("default") or {}


def heuristic_plan(focus: str, lookback_hours: int) -> AnalysisPlan:
    preset = _match_preset(focus or "大盘")
    sources = load_yaml("sources.yml")
    budgets = load_yaml("budgets.yml")
    benchmarks = [row["symbol"] for row in sources.get("benchmarks") or []]
    tickers = list(preset.get("tickers") or [])
    cap = int(budgets.get("max_tickers") or 18)
    return AnalysisPlan(
        sectors=list(preset.get("sectors") or ["综合"]),
        keywords=list(preset.get("keywords") or [focus or "市场"]),
        newsQueries=list(preset.get("news_queries") or [focus or "stock market"]),
        tickers=tickers[:cap],
        benchmarks=benchmarks,
        lookbackHours=lookback_hours,
        maxItemsPerSource=int(budgets.get("max_items_per_source") or 20),
    )


def plan_analysis(focus: str, lookback_hours: int) -> tuple[AnalysisPlan, str, list[str]]:
    base = heuristic_plan(focus, lookback_hours)
    warnings: list[str] = []
    if not env("AI_API_KEY"):
        warnings.append("未配置 AI_API_KEY，使用规则规划")
        return base, "heuristic", warnings
    prompt = (
        "你是市场研究规划器。根据用户侧重点，输出 JSON 对象，不要 markdown。"
        "字段: sectors(字符串数组,2-5个板块),"
        "keywords(中英检索词,6-14个), newsQueries(1-2条搜索词),"
        "tickers(股票或指数代码, A股用 sh/sz 前缀如 sh603986, 美股用 Yahoo 代码如 MU),"
        f"lookbackHours(整数,默认{lookback_hours})。"
        "只规划公开新闻和行情，不要微博或 X。"
        f"用户侧重点: {focus or '泛市场扫描'}"
    )
    try:
        model = pick_model("planner", env("AI_MODEL_PLANNER"))
        raw = chat(
            [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": prompt},
            ],
            model=model,
            max_tokens=int(load_yaml("budgets.yml").get("planner_max_tokens") or 700),
        )
        data = parse_json_object(raw)
        merged = base.model_dump()
        for key in ("sectors", "keywords", "newsQueries", "tickers"):
            value = data.get(key)
            if isinstance(value, list) and value:
                merged[key] = [str(item).strip() for item in value if str(item).strip()]
        if isinstance(data.get("lookbackHours"), int) and data["lookbackHours"] > 0:
            merged["lookbackHours"] = data["lookbackHours"]
        cap = int(load_yaml("budgets.yml").get("max_tickers") or 18)
        merged["tickers"] = merged["tickers"][:cap]
        merged["newsQueries"] = merged["newsQueries"][: int(load_yaml("budgets.yml").get("max_google_queries") or 2)]
        return AnalysisPlan.model_validate(merged), model, warnings
    except (LLMError, ValueError) as exc:
        warnings.append(f"规划模型失败，回退规则规划: {exc}")
        return base, "heuristic", warnings
