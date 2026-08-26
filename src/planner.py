from __future__ import annotations

import re

from src.ingest.quotes import normalize_symbol
from src.llm import LLMError, chat, model_debug, parse_json_object, resolve_model
from src.models import AnalysisPlan
from src.settings import env, load_yaml

_CODE = re.compile(r"(?:sh|sz)?(\d{6})", re.I)
_THEME_SUFFIX = re.compile(r"(相关|板块|行业|概念|etf)$", re.I)
_TASK_SUFFIX = re.compile(r"(分析|扫描|复盘|解读|跟踪|研究)$")
_TAPE_PATTERNS = (
    re.compile(r"资金(流入|流出|流向)"),
    re.compile(r"主力(资金|净流入|净流出)"),
    re.compile(r"北向(资金|流入|流出)"),
    re.compile(r"尾盘"),
    re.compile(r"拉升"),
    re.compile(r"跳水"),
    re.compile(r"涨停"),
    re.compile(r"跌停"),
    re.compile(r"龙虎榜"),
    re.compile(r"异动"),
    re.compile(r"爆量"),
    re.compile(r"连板"),
    re.compile(r"热门股"),
    re.compile(r"板块轮动"),
    re.compile(r"高开低走"),
    re.compile(r"冲高回落"),
    re.compile(r"反包"),
)


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


def _tape_preset(focus: str) -> dict | None:
    data = load_yaml("presets.yml")
    for row in data.get("tape_presets") or []:
        for token in row.get("match") or []:
            if _token_in_focus(str(token), focus):
                return row
    return None


def _generic_tape(focus: str) -> dict:
    text = (focus or "").strip() or "盘面"
    return {
        "sectors": [text, "相关热门板块", "相关活跃个股"],
        "keywords": [text, "A股", "板块", "个股", "资金"],
        "news_queries": [
            f"今日 A股 {text} 板块",
            f"今日 {text} 个股",
            f"{text} 热门股",
        ],
        "etfs": ["sh510300", "sh510500", "sz159915"],
        "tickers": [],
    }


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


def is_tape_focus(focus: str) -> bool:
    """True for session/tape prompts like 资金流入分析 or 尾盘拉升."""
    text = (focus or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _TAPE_PATTERNS)


def is_stock_focus(focus: str) -> bool:
    """True when the user named a company/ticker rather than a theme or tape scan."""
    text = (focus or "").strip()
    if not text or is_tape_focus(text):
        return False
    core = _THEME_SUFFIX.sub("", text).strip() or text
    core = _TASK_SUFFIX.sub("", core).strip() or core
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


def focus_kind(focus: str) -> str:
    if is_stock_focus(focus):
        return "stock"
    if is_tape_focus(focus):
        return "tape"
    return "theme"


def _templated_query(key: str, focus: str, fallback: str = "") -> str:
    sources = load_yaml("sources.yml")
    template = str(sources.get(key) or fallback)
    if not template:
        return ""
    return template.replace("{focus}", (focus or "").strip() or "A股")


def _sentiment_query(focus: str) -> str:
    return _templated_query("sentiment_query", focus, "{focus} 市场情绪 北向资金 风险偏好")


def _extra_queries(focus: str) -> list[str]:
    text = (focus or "").strip() or "A股"
    rows = [
        _templated_query("official_query", text, "{focus} (证监会 OR 交易所公告 OR 央行 OR 新华社)"),
        _templated_query("volume_query", text, "{focus} 成交量 放量 缩量 量能"),
        _templated_query("northbound_query", text, "{focus} 北向资金 外资 沪股通 深股通"),
        _sentiment_query(text),
        _templated_query("blog_query", text, "{focus} (专栏 OR 博客 OR 复盘 OR 点评)"),
    ]
    return [item for item in rows if item]


def _enrich_queries(queries: list[str], focus: str, cap: int) -> list[str]:
    """Keep the most specific queries, then force official / volume / flow / blog coverage."""
    head = list(queries[:3])
    rest = list(queries[3:])
    return _dedupe([*head, *_extra_queries(focus), *rest])[:cap]


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
    cap = int(budgets.get("max_tickers") or 24)
    query_cap = int(budgets.get("max_google_queries") or 5)
    kind = focus_kind(focus)
    named = _named_preset(focus)
    foreign = _other_theme_symbols(focus) if kind == "theme" else set()
    primary = _alias_symbol(focus)
    tickers = [normalize_symbol(item) for item in plan.get("tickers") or []]
    etfs = [normalize_symbol(item) for item in plan.get("etfs") or []]
    if named and kind != "tape":
        tickers = _dedupe([*tickers, *[normalize_symbol(item) for item in (named.get("tickers") or [])]])
        etfs = _dedupe(
            [*[normalize_symbol(item) for item in (named.get("etfs") or [])], *etfs]
        )
        tickers = [item for item in tickers if item not in foreign]
        etfs = [item for item in etfs if item not in foreign]
    elif named and kind == "tape":
        etfs = _dedupe([*etfs, *[normalize_symbol(item) for item in (named.get("etfs") or [])]])
        tickers = _dedupe([*tickers, *[normalize_symbol(item) for item in (named.get("tickers") or [])]])
    if primary:
        tickers = _dedupe([primary, *tickers])
    if kind == "stock" and not named:
        tickers = _dedupe([primary] if primary else tickers[:1])
        etfs = etfs or []
    if kind == "tape":
        tape = _tape_preset(focus) or _generic_tape(focus)
        etfs = _dedupe([*etfs, *[normalize_symbol(item) for item in (tape.get("etfs") or [])]])
    queries = _enrich_queries(list(plan.get("newsQueries") or []), focus or "A股", query_cap)
    plan["tickers"] = [item for item in tickers if item][:cap]
    plan["etfs"] = [item for item in etfs if item][:cap]
    plan["newsQueries"] = queries
    plan["focusKind"] = kind
    return plan


def heuristic_plan(focus: str, lookback_hours: int) -> AnalysisPlan:
    text = (focus or "").strip() or "大盘"
    kind = focus_kind(text)
    sources = load_yaml("sources.yml")
    budgets = load_yaml("budgets.yml")
    benchmarks = [row["symbol"] for row in sources.get("benchmarks") or []]
    query_cap = int(budgets.get("max_google_queries") or 5)
    ticker_cap = int(budgets.get("max_tickers") or 24)
    if kind == "tape":
        preset = _tape_preset(text) or _generic_tape(text)
        sectors = list(preset.get("sectors") or [text])
        keywords = _dedupe(
            [*(preset.get("keywords") or [text]), "市场情绪", "北向资金", "风险偏好", "成交量", "放量"]
        )
        news_queries = _dedupe(list(preset.get("news_queries") or [f"今日 {text} 个股 板块"]))
        tickers = [normalize_symbol(symbol) for symbol in (preset.get("tickers") or [])]
        etfs = [normalize_symbol(symbol) for symbol in (preset.get("etfs") or [])]
        named = _named_preset(text)
        if named:
            tickers = _dedupe([*tickers, *[normalize_symbol(item) for item in (named.get("tickers") or [])]])
            etfs = _dedupe([*etfs, *[normalize_symbol(item) for item in (named.get("etfs") or [])]])
            sectors = _dedupe([*sectors, *(named.get("sectors") or [])])
    else:
        preset = _match_preset(text)
        tickers = [normalize_symbol(symbol) for symbol in (preset.get("tickers") or [])]
        etfs = [normalize_symbol(symbol) for symbol in (preset.get("etfs") or [])]
        primary = _alias_symbol(text)
        stock = kind == "stock"
        if primary:
            tickers = _dedupe([primary, *tickers])
        if stock:
            sectors = list(preset.get("sectors") or [f"{text}及相关行业"])
            keywords = _dedupe(
                [
                    text,
                    *[str(item) for item in (preset.get("keywords") or [])],
                    "市场情绪",
                    "北向资金",
                    "风险偏好",
                    "成交量",
                ]
            )
            news_queries = _dedupe(
                [
                    f"{text} 业绩 订单 研报",
                    f"{text} {sectors[0]} 行业 政策",
                    *[str(item) for item in (preset.get("news_queries") or [])],
                ]
            )
            if not _named_preset(text):
                tickers = [primary] if primary else tickers[:1]
        else:
            sectors = list(preset.get("sectors") or ["综合"])
            keywords = _dedupe(
                [
                    *(preset.get("keywords") or [text]),
                    "市场情绪",
                    "北向资金",
                    "风险偏好",
                    "外资",
                    "成交量",
                ]
            )
            news_queries = _dedupe(list(preset.get("news_queries") or [text]))
    news_queries = _enrich_queries(news_queries, text, query_cap)
    return AnalysisPlan(
        sectors=sectors,
        keywords=keywords,
        newsQueries=news_queries,
        tickers=tickers[:ticker_cap],
        etfs=etfs[:ticker_cap],
        benchmarks=benchmarks,
        lookbackHours=lookback_hours,
        maxItemsPerSource=int(budgets.get("max_items_per_source") or 20),
        focusKind=kind,
    )


def plan_analysis(focus: str, lookback_hours: int) -> tuple[AnalysisPlan, str, list[str]]:
    base = heuristic_plan(focus, lookback_hours)
    warnings: list[str] = []
    if not env("AI_API_KEY"):
        warnings.append("未配置 AI_API_KEY，使用规则规划")
        return base, "heuristic", warnings
    kind = focus_kind(focus)
    kind_label = {
        "stock": "单一股票及其所属行业",
        "tape": "盘面现象/交易行为（按当天新闻找板块和个股，不是固定行业）",
        "theme": "主题/板块",
    }[kind]
    prompt = (
        "你是市场研究规划器。根据用户侧重点，输出 JSON 对象，不要 markdown。"
        "字段: sectors(字符串数组,2-6个),"
        "keywords(中英检索词,8-16个),"
        "newsQueries(5-7条搜索词，要能搜到当天中文财经新闻，覆盖官方/政策、量能、北向或外资、市场情绪，并包含专栏/复盘/博客类公开文章),"
        "tickers(8-18个股票代码, A股用 sh/sz 前缀如 sh600276, 美股用 Yahoo 代码),"
        "etfs(相关 ETF 代码, A股用 sh/sz 前缀)。"
        f"侧重点类型: {kind_label}。"
        "若是单一股票名称或代码: tickers 第一项必须是该股, sectors 写其行业, "
        "newsQueries 覆盖该公司、所属行业、官方公告、量能以及市场情绪/北向资金。"
        "若是主题(如医药、存储): tickers 与 etfs 必须属于该主题, 禁止塞入无关行业标的。"
        "若是盘面现象（资金流入、尾盘拉升、涨停、龙虎榜、异动等）: "
        "不要理解成银行/券商/保险等金融股专题，除非用户明确写了金融。"
        "newsQueries 必须带「今日」或当天，分别搜 资金流入/现象对应的板块、热门个股、原因。"
        "tickers 填写新闻里常被点名、且与该现象相符的活跃股，覆盖多个行业，不要默认银行股。"
        "etfs 用能代表当天主线的行业或宽基 ETF。"
        f"lookbackHours 默认{lookback_hours}。只规划公开新闻和行情，不要微博或 X。"
        f"用户侧重点: {focus or '泛市场扫描'}"
    )
    try:
        model = resolve_model("planner")
        print(f"calling planner {model_debug(model)} kind={kind}", flush=True)
        raw = chat(
            [
                {"role": "system", "content": "Return JSON only. Prefer today's A-share tape, not a canned industry basket."},
                {"role": "user", "content": prompt},
            ],
            model=model,
            max_tokens=int(load_yaml("budgets.yml").get("planner_max_tokens") or 1600),
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
            f"planner ok kind={kind} sectors={merged.get('sectors')} "
            f"tickers={merged.get('tickers')} etfs={merged.get('etfs')} "
            f"queries={merged.get('newsQueries')}",
            flush=True,
        )
        return AnalysisPlan.model_validate(merged), model, warnings
    except (LLMError, ValueError) as exc:
        print(f"planner failed: {exc}", flush=True)
        warnings.append(f"规划模型失败，回退规则规划: {exc}")
        return base, "heuristic", warnings
