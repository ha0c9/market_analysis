from __future__ import annotations

import re

from src.ingest.quotes import normalize_symbol
from src.llm import LLMError, chat, model_debug, parse_json_object, resolve_model
from src.models import AnalysisPlan
from src.settings import env, load_yaml

_CODE = re.compile(r"(?:sh|sz)?(\d{6})", re.I)
_THEME_SUFFIX = re.compile(r"(相关|板块|行业|概念|etf)$", re.I)


def _token_in_focus(token: str, focus: str) -> bool:
    needle = (token or "").strip().lower()
    haystack = (focus or "").strip().lower()
    if not needle or not haystack:
        return False
    if len(needle) <= 2 and needle.isascii():
        return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None
    return needle in haystack


def _named_preset(focus: str) -> dict | None:
    data = load_yaml("presets.yml")
    for row in data.get("presets") or []:
        for token in row.get("match") or []:
            if _token_in_focus(str(token), focus):
                return row
    return None


def _match_preset(focus: str) -> dict:
    return _named_preset(focus) or (load_yaml("presets.yml").get("default") or {})


def _preset_symbols(preset: dict | None) -> set[str]:
    if not preset:
        return set()
    return {
        normalize_symbol(str(item))
        for item in [*(preset.get("tickers") or []), *(preset.get("etfs") or [])]
        if str(item).strip()
    }


def _other_theme_symbols(focus: str) -> set[str]:
    named = _named_preset(focus)
    if not named:
        return set()
    own = _preset_symbols(named)
    data = load_yaml("presets.yml")
    others: set[str] = set()
    for row in data.get("presets") or []:
        others |= _preset_symbols(row)
    return others - own


def _alias_symbol(focus: str) -> str:
    aliases = load_yaml("presets.yml").get("stock_aliases") or {}
    text = focus.strip()
    if text in aliases:
        return normalize_symbol(str(aliases[text]))
    best = ""
    best_len = 0
    for name, symbol in aliases.items():
        key = str(name)
        if len(key) < 2:
            continue
        if key in text and len(key) > best_len:
            best = normalize_symbol(str(symbol))
            best_len = len(key)
    if best:
        return best
    match = _CODE.search(text)
    if match:
        return normalize_symbol(match.group(0))
    return ""


def is_stock_focus(focus: str) -> bool:
    """True when the user named a company/ticker rather than a theme."""
    text = (focus or "").strip()
    if not text:
        return False
    core = _THEME_SUFFIX.sub("", text).strip() or text
    data = load_yaml("presets.yml")
    theme_tokens = {str(token).lower() for row in data.get("presets") or [] for token in (row.get("match") or [])}
    if core.lower() in theme_tokens or text.lower() in theme_tokens:
        return False
    if _alias_symbol(text) or _CODE.search(text):
        return True
    if re.search(r"(股份|集团|控股)", text):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]{2,10}", core) and core.lower() not in theme_tokens:
        return True
    return False


def _sentiment_query(focus: str) -> str:
    sources = load_yaml("sources.yml")
    template = str(sources.get("sentiment_query") or "{focus} 市场情绪 北向资金 风险偏好")
    return template.replace("{focus}", focus.strip() or "A股")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for item in values:
        text = str(item).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        rows.append(text)
    return rows


def _cap_plan(plan: dict, focus: str) -> dict:
    budgets = load_yaml("budgets.yml")
    cap = int(budgets.get("max_tickers") or 18)
    query_cap = int(budgets.get("max_google_queries") or 3)
    named = _named_preset(focus)
    foreign = _other_theme_symbols(focus)
    primary = _alias_symbol(focus)
    tickers = [normalize_symbol(item) for item in plan.get("tickers") or []]
    etfs = [normalize_symbol(item) for item in plan.get("etfs") or []]
    if named:
        tickers = _dedupe([*tickers, *[normalize_symbol(item) for item in (named.get("tickers") or [])]])
        etfs = _dedupe(
            [*[normalize_symbol(item) for item in (named.get("etfs") or [])], *etfs]
        )
        tickers = [item for item in tickers if item not in foreign]
        etfs = [item for item in etfs if item not in foreign]
    if primary:
        tickers = _dedupe([primary, *tickers])
    if is_stock_focus(focus) and not named:
        tickers = _dedupe([primary] if primary else tickers[:1])
        etfs = []
    queries = _dedupe([*(plan.get("newsQueries") or []), _sentiment_query(focus or "A股")])
    plan["tickers"] = [item for item in tickers if item][:cap]
    plan["etfs"] = [item for item in etfs if item][:cap]
    plan["newsQueries"] = queries[:query_cap]
    return plan


def heuristic_plan(focus: str, lookback_hours: int) -> AnalysisPlan:
    text = (focus or "").strip() or "大盘"
    preset = _match_preset(text)
    sources = load_yaml("sources.yml")
    budgets = load_yaml("budgets.yml")
    benchmarks = [row["symbol"] for row in sources.get("benchmarks") or []]
    tickers = [normalize_symbol(symbol) for symbol in (preset.get("tickers") or [])]
    etfs = [normalize_symbol(symbol) for symbol in (preset.get("etfs") or [])]
    primary = _alias_symbol(text)
    stock = is_stock_focus(text)
    if primary:
        tickers = _dedupe([primary, *tickers])
    if stock:
        sectors = list(preset.get("sectors") or [f"{text}及相关行业"])
        keywords = _dedupe(
            [text, *[str(item) for item in (preset.get("keywords") or [])], "市场情绪", "北向资金", "风险偏好"]
        )
        news_queries = _dedupe(
            [
                f"{text} 业绩 订单 研报",
                f"{text} {sectors[0]} 行业 政策",
                _sentiment_query(text),
                *[str(item) for item in (preset.get("news_queries") or [])],
            ]
        )
        if not _named_preset(text):
            tickers = [primary] if primary else tickers[:1]
            etfs = []
    else:
        sectors = list(preset.get("sectors") or ["综合"])
        keywords = _dedupe(
            [*(preset.get("keywords") or [text]), "市场情绪", "北向资金", "风险偏好", "外资"]
        )
        news_queries = _dedupe(
            [_sentiment_query(text), *(preset.get("news_queries") or [text])]
        )
    return AnalysisPlan(
        sectors=sectors,
        keywords=keywords,
        newsQueries=news_queries[: int(budgets.get("max_google_queries") or 3)],
        tickers=tickers[: int(budgets.get("max_tickers") or 18)],
        etfs=etfs[: int(budgets.get("max_tickers") or 18)],
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
    kind = "单一股票及其所属行业" if is_stock_focus(focus) else "主题/板块"
    prompt = (
        "你是市场研究规划器。根据用户侧重点，输出 JSON 对象，不要 markdown。"
        "字段: sectors(字符串数组,2-5个板块),"
        "keywords(中英检索词,6-14个,必须包含市场情绪/资金面词如 北向资金 风险偏好 情绪),"
        "newsQueries(2-3条搜索词,其中至少一条明确搜市场情绪或资金流向),"
        "tickers(股票代码, A股用 sh/sz 前缀如 sh600276, 美股用 Yahoo 代码),"
        "etfs(相关行业 ETF 代码, A股用 sh/sz 前缀)。"
        f"侧重点类型: {kind}。"
        "若是单一股票名称或代码: tickers 第一项必须是该股, sectors 写其行业, "
        "newsQueries 覆盖该公司、所属行业、以及市场情绪/北向资金。"
        "若是主题(如医药、存储): tickers 与 etfs 必须属于该主题, 禁止塞入无关行业标的。"
        f"lookbackHours 默认{lookback_hours}。只规划公开新闻和行情，不要微博或 X。"
        f"用户侧重点: {focus or '泛市场扫描'}"
    )
    try:
        model = resolve_model("planner")
        print(f"calling planner {model_debug(model)} kind={kind}", flush=True)
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
        for key in ("sectors", "keywords", "newsQueries", "tickers", "etfs"):
            value = data.get(key)
            if isinstance(value, list) and value:
                merged[key] = [str(item).strip() for item in value if str(item).strip()]
        if isinstance(data.get("lookbackHours"), int) and data["lookbackHours"] > 0:
            merged["lookbackHours"] = data["lookbackHours"]
        merged = _cap_plan(merged, focus)
        print(
            f"planner ok sectors={len(merged.get('sectors') or [])} "
            f"tickers={merged.get('tickers')} etfs={merged.get('etfs')}",
            flush=True,
        )
        return AnalysisPlan.model_validate(merged), model, warnings
    except (LLMError, ValueError) as exc:
        print(f"planner failed: {exc}", flush=True)
        warnings.append(f"规划模型失败，回退规则规划: {exc}")
        return base, "heuristic", warnings
