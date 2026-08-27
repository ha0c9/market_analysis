from __future__ import annotations

import unittest

from src.distill import distill_news
from src.ingest.quotes import normalize_symbol, parse_tencent_body
from src.models import NewsItem
from src.planner import heuristic_plan
from src.llm import model_debug, public_url_parts, resolve_model
from src.settings import ai_base_url


class SettingsTests(unittest.TestCase):
    def test_base_url_is_not_rewritten(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"AI_BASE_URL": "https://node-hk.sssaiapi.com/api/v1"}):
            self.assertEqual(ai_base_url(), "https://node-hk.sssaiapi.com/api/v1")
        with patch.dict(os.environ, {"AI_BASE_URL": "https://node-hk.sssaiapi.com/api"}):
            self.assertEqual(ai_base_url(), "https://node-hk.sssaiapi.com/api")


class ModelResolveTests(unittest.TestCase):
    def test_uses_secret_as_is(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {"AI_MODEL_PLANNER": "deepseek-v4-flash", "AI_MODEL_SYNTHESIZER": "deepseek-v4-pro"},
        ):
            self.assertEqual(resolve_model("planner"), "deepseek-v4-flash")
            self.assertEqual(resolve_model("synthesizer"), "deepseek-v4-pro")

    def test_empty_secrets_default_to_deepseek_v4_flash(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"AI_MODEL_PLANNER": "", "AI_MODEL_SYNTHESIZER": ""}):
            self.assertEqual(resolve_model("planner"), "deepseek-v4-flash")
            self.assertEqual(resolve_model("synthesizer"), "deepseek-v4-flash")

    def test_synthesizer_reuses_planner_secret(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"AI_MODEL_PLANNER": "deepseek-v4-flash", "AI_MODEL_SYNTHESIZER": ""}):
            self.assertEqual(resolve_model("synthesizer"), "deepseek-v4-flash")

    def test_public_url_parts(self) -> None:
        self.assertEqual(
            public_url_parts("https://node-hk.sssaiapi.com/api/v1"),
            "scheme=https host=node-hk.sssaiapi.com path=/api/v1",
        )

    def test_model_debug_flags(self) -> None:
        self.assertIn("flags=deepseek,flash,vision", model_debug("deepseek-v4-flash-vision-exp"))
        self.assertIn("flags=grok", model_debug("grok-4.6"))
        self.assertIn("spaced=true", model_debug("Grok 4.6"))

    def test_content_ignores_reasoning_chain(self) -> None:
        from src.llm import _content_from_message

        self.assertEqual(_content_from_message({"content": "", "reasoning_content": "think then JSON"}), "")
        self.assertEqual(_content_from_message({"content": '{"ok": true}'}), '{"ok": true}')



TENCENT = (
    'v_sh000001="1~上证指数~000001~3889.60~3882.01~3863.37~0~0~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~~20260825133157~7.59~0.20~3896.21~3850.86~";'
)


class QuoteTests(unittest.TestCase):
    def test_normalize_symbol(self) -> None:
        self.assertEqual(normalize_symbol("603986.SH"), "sh603986")
        self.assertEqual(normalize_symbol("000001.SZ"), "sz000001")
        self.assertEqual(normalize_symbol("MU"), "MU")

    def test_parse_tencent(self) -> None:
        rows = parse_tencent_body(TENCENT)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "上证指数")
        self.assertAlmostEqual(rows[0].changePct or 0, 0.20)
        self.assertEqual(rows[0].asOf, "2026-08-25T05:31:57Z")

    def test_snapshot_follows_focus_not_global_sectors(self) -> None:
        from src.ingest.quotes import snapshot_from_rows
        from src.models import QuoteRow

        rows = [
            QuoteRow(symbol="sh000001", name="上证指数", changePct=0.1, asOf="2026-08-25T05:31:57Z"),
            QuoteRow(symbol="sh512480", name="半导体ETF", changePct=-0.2, asOf="2026-08-25T05:31:57Z"),
            QuoteRow(symbol="sh512010", name="医药ETF", changePct=0.5, asOf="2026-08-25T05:31:57Z"),
            QuoteRow(symbol="sh600276", name="恒瑞医药", changePct=1.0, asOf="2026-08-25T05:31:57Z"),
            QuoteRow(symbol="sh603986", name="兆易创新", changePct=3.0, asOf="2026-08-25T05:31:57Z"),
        ]
        snap = snapshot_from_rows(
            rows,
            "test",
            benchmark_ids=["sh000001"],
            focus_ids=["sh512010", "sh600276"],
            etf_ids=["sh512010"],
        )
        self.assertEqual(snap["asOf"], "2026-08-25T05:31:57Z")
        self.assertEqual([row["symbol"] for row in snap["sectors"]], ["sh512010"])
        self.assertEqual([row["symbol"] for row in snap["tickers"]], ["sh600276"])
        self.assertNotIn("sh512480", [row["symbol"] for row in snap["sectors"]])
        self.assertNotIn("sh603986", [row["symbol"] for row in snap["tickers"]])

    def test_asia_benchmarks_kospi_and_nikkei(self) -> None:
        from src.ingest.quotes import snapshot_from_rows
        from src.models import QuoteRow
        from src.planner import heuristic_plan

        plan = heuristic_plan("医药相关", 36)
        self.assertIn("^N225", plan.benchmarks)
        self.assertIn("^KS11", plan.benchmarks)
        snap = snapshot_from_rows(
            [
                QuoteRow(symbol="sh000001", name="上证指数", changePct=0.1, asOf="2026-08-25T05:31:57Z"),
                QuoteRow(symbol="^N225", name="Nikkei 225", changePct=0.49, asOf="2026-08-25T06:00:00Z"),
                QuoteRow(symbol="^KS11", name="KOSPI Composite Index", changePct=-0.42, asOf="2026-08-25T06:30:00Z"),
                QuoteRow(symbol="sh600276", name="恒瑞医药", changePct=1.0, asOf="2026-08-25T05:31:57Z"),
            ],
            "test",
            benchmark_ids=plan.benchmarks,
            focus_ids=["sh600276"],
        )
        names = {row["symbol"]: row["name"] for row in snap["benchmarks"]}
        self.assertEqual(names["^N225"], "日经225")
        self.assertEqual(names["^KS11"], "首尔综合指数")
        self.assertEqual(
            [row["symbol"] for row in snap["benchmarks"]],
            ["sh000001", "^N225", "^KS11"],
        )
        self.assertNotIn("^N225", [row["symbol"] for row in snap["tickers"]])
        self.assertNotIn("^KS11", [row["symbol"] for row in snap["tickers"]])

    def test_yahoo_kospi_change_uses_prior_daily_bar(self) -> None:
        from src.ingest.quotes import previous_close_from_yahoo

        closes = [6869.83, 6471.17, 6852.58, 6912.95, 6696.96, 6742.74]
        prev = previous_close_from_yahoo({"chartPreviousClose": 6869.83}, closes, 6742.74)
        self.assertAlmostEqual(prev or 0, 6696.96)
        change = (6742.74 / prev - 1.0) * 100
        self.assertAlmostEqual(change, 0.684, places=2)


class DistillTests(unittest.TestCase):
    def test_keyword_and_dedup(self) -> None:
        items = [
            NewsItem(title="存储芯片涨价 NAND 合约价上修", source="a", snippet="NAND"),
            NewsItem(title="存储芯片涨价 NAND 合约价上修！！", source="b", snippet="重复"),
            NewsItem(title="无关的体育新闻", source="c", snippet="足球"),
        ]
        kept = distill_news(items, ["存储", "NAND"], 48)
        self.assertGreaterEqual(len(kept), 1)
        self.assertIn("存储", kept[0].title)


class OutlookNormalizeTests(unittest.TestCase):
    def test_coerces_loose_llm_outlook(self) -> None:
        from src.synthesize import normalize_sector_outlook

        news = [
            NewsItem(
                title="又一“全球首创”疗法获批",
                source="x",
                url="https://example.com/a",
                publishedAt="2025-07-03T07:00:00Z",
                snippet="创新药",
            )
        ]
        rows = normalize_sector_outlook(
            [
                {
                    "sector": "创新药",
                    "heat": "high",
                    "heatScore": 8,
                    "priceAction": "恒瑞医药下跌0.58%，礼来上涨4.26%。",
                    "direction": "neutral",
                    "calibration": "confirming",
                    "narrative": "板块分化。",
                    "evidence": ["又一“全球首创”疗法获批 (2025-07-03T07:00:00Z)"],
                    "counterEvidence": "恒瑞医药股价下跌，能力仍有疑虑。",
                    "confidence": 0.6,
                    "invalidatedIf": "政策转向",
                }
            ],
            news,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["heat"], 1)
        self.assertEqual(rows[0]["heatScore"], 0.8)
        self.assertEqual(rows[0]["priceAction"], "mixed")
        self.assertEqual(rows[0]["direction"], "unclear")
        self.assertEqual(rows[0]["evidence"][0]["url"], "https://example.com/a")
        self.assertEqual(rows[0]["counterEvidence"][0]["claim"][:4], "恒瑞医药")


class PlannerTests(unittest.TestCase):
    def test_storage_preset(self) -> None:
        plan = heuristic_plan("存储相关", 36)
        self.assertTrue(any("存储" in sector for sector in plan.sectors))
        self.assertIn("sh603986", plan.tickers)
        self.assertTrue(any("情绪" in query or "北向" in query for query in plan.newsQueries))

    def test_pharma_preset_not_semiconductor(self) -> None:
        from src.planner import is_stock_focus

        self.assertFalse(is_stock_focus("医药"))
        self.assertFalse(is_stock_focus("医药相关"))
        plan = heuristic_plan("医药相关", 36)
        self.assertIn("sh600276", plan.tickers)
        self.assertNotIn("sh603986", plan.tickers)
        self.assertIn("sh512010", plan.etfs)
        self.assertNotIn("SOXX", plan.etfs)
        self.assertTrue(any("情绪" in query or "北向" in query for query in plan.newsQueries))

    def test_stock_name_focus(self) -> None:
        from src.planner import is_stock_focus

        self.assertTrue(is_stock_focus("恒瑞医药"))
        self.assertTrue(is_stock_focus("sh600276"))
        plan = heuristic_plan("恒瑞医药", 36)
        self.assertEqual(plan.tickers[0], "sh600276")
        self.assertTrue(any("恒瑞" in query for query in plan.newsQueries))
        self.assertTrue(any("情绪" in query or "北向" in query for query in plan.newsQueries))
        self.assertIn("sh512010", plan.etfs)
        self.assertNotIn("sh603986", plan.tickers)

    def test_tape_focus_is_not_a_stock_or_financials_basket(self) -> None:
        from src.planner import focus_kind, is_stock_focus, is_tape_focus

        self.assertTrue(is_tape_focus("资金流入分析"))
        self.assertFalse(is_stock_focus("资金流入分析"))
        self.assertEqual(focus_kind("资金流入分析"), "tape")
        self.assertTrue(is_tape_focus("尾盘拉升"))
        self.assertFalse(is_stock_focus("尾盘拉升"))
        self.assertEqual(focus_kind("尾盘拉升"), "tape")
        self.assertTrue(is_stock_focus("恒瑞医药"))

        inflow = heuristic_plan("资金流入分析", 36)
        self.assertEqual(inflow.focusKind, "tape")
        self.assertTrue(any("资金流入" in query and "板块" in query for query in inflow.newsQueries))
        self.assertTrue(any("个股" in query for query in inflow.newsQueries))
        self.assertNotIn("sh600519", inflow.tickers)
        self.assertTrue(inflow.etfs)
        blob = " ".join(inflow.sectors + inflow.keywords).lower()
        self.assertNotRegex(blob, r"银行|券商|保险")

        late = heuristic_plan("尾盘拉升", 36)
        self.assertEqual(late.focusKind, "tape")
        self.assertTrue(any("尾盘" in query for query in late.newsQueries))
        self.assertTrue(any("个股" in query for query in late.newsQueries))
        self.assertNotIn("sh600519", late.tickers)

    def test_plan_covers_official_volume_and_blog_queries(self) -> None:
        plan = heuristic_plan("医药相关", 36)
        blob = " ".join(plan.newsQueries)
        self.assertIn("证监会", blob)
        self.assertIn("成交量", blob)
        self.assertTrue("北向" in blob or "外资" in blob)
        self.assertTrue("专栏" in blob or "博客" in blob or "复盘" in blob)
        self.assertLessEqual(len(plan.newsQueries), 8)
        self.assertGreaterEqual(len(plan.newsQueries), 5)


class SourceWeightTests(unittest.TestCase):
    def test_classify_official_media_and_blog(self) -> None:
        from src.ingest.news import classify_source

        self.assertEqual(classify_source("新华社", "https://www.xinhuanet.com/fortune")[0], "official")
        self.assertGreaterEqual(classify_source("新华社", "https://www.xinhuanet.com/fortune")[1], 2.5)
        self.assertEqual(classify_source("Reuters", "https://www.reuters.com/business")[0], "major_media")
        self.assertEqual(classify_source("个人复盘", "https://www.zhihu.com/p/123")[0], "blog")
        self.assertEqual(classify_source("Google News / 医药", "https://news.google.com/rss")[0], "google_news")
        self.assertEqual(classify_source("新华网财经", "http://www.xinhuanet.com/fortune/a")[0], "official")
        self.assertEqual(classify_source("华尔街见闻", "https://wallstreetcn.com/a")[0], "major_media")
        self.assertEqual(classify_source("财联社标红", "https://www.cls.cn/detail/1")[0], "major_media")

    def test_cls_red_telegraph_outranks_plain_flash(self) -> None:
        from src.ingest.cls import is_cls_red, parse_cls_roll

        payload = {
            "errno": 0,
            "data": {
                "roll_data": [
                    {
                        "id": 1,
                        "title": "普通快讯 存储芯片",
                        "brief": "一般行情播报",
                        "content": "一般行情播报",
                        "ctime": 1787716800,
                        "level": "C",
                        "bold": 0,
                    },
                    {
                        "id": 2,
                        "title": "存储芯片 工信部标红快讯",
                        "brief": "【工信部：动力电池退役法规】财联社电，将研究起草相关行政法规。",
                        "content": "将研究起草相关行政法规。",
                        "ctime": 1787716900,
                        "level": "B",
                        "bold": 0,
                    },
                ]
            },
        }
        self.assertTrue(is_cls_red({"level": "A"}))
        self.assertTrue(is_cls_red({"level": "B"}))
        self.assertFalse(is_cls_red({"level": "C", "bold": 0}))
        rows = parse_cls_roll(payload, limit=10)
        self.assertEqual(len(rows), 2)
        red = next(item for item in rows if item.highlight)
        plain = next(item for item in rows if not item.highlight)
        self.assertEqual(red.source, "财联社标红")
        self.assertIn("工信部", red.title)
        self.assertTrue(red.url.endswith("/detail/2"))
        self.assertGreater(red.sourceWeight, plain.sourceWeight)
        kept = distill_news(rows, ["存储"], 48)
        self.assertEqual(kept[0].highlight, True)

    def test_google_skips_english_edition_for_chinese_queries(self) -> None:
        from src.ingest.news import compact_http_error, google_editions_for_query

        templates = {
            "zh": "https://news.google.com/rss/search?q={query}&hl=zh-CN&gl=HK&ceid=HK:zh-Hans",
            "en": "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
        }
        self.assertEqual(
            google_editions_for_query("今日 存储芯片 板块 行情 资金流入", templates),
            ["zh"],
        )
        self.assertEqual(
            google_editions_for_query("NAND DRAM memory chip shortage", templates),
            ["en"],
        )
        long_503 = (
            "Server error '503 Service Unavailable' for url 'https://news.google.com/rss/search?q=foo'\n"
            "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503"
        )
        self.assertEqual(compact_http_error(RuntimeError(long_503)), "503 限流")
        self.assertEqual(compact_http_error(OSError("[Errno -2] Name or service not known")), "DNS 失败")
        self.assertEqual(compact_http_error(RuntimeError("403 Forbidden for url 'https://weibo.com/ajax/statuses/hot_band'")), "403 拒绝")

    def test_google_zh_fallback_swaps_cn_edition(self) -> None:
        from src.ingest.news import _google_fallback_urls

        urls = _google_fallback_urls(
            "https://news.google.com/rss/search?q=foo&hl=zh-CN&gl=HK&ceid=HK:zh-Hans",
            "zh",
        )
        self.assertEqual(urls[0].count("gl=HK"), 1)
        self.assertTrue(any("gl=US" in url for url in urls))

    def test_official_outranks_blog_in_distill(self) -> None:
        items = [
            NewsItem(
                title="存储芯片涨价 NAND 合约价上修 专栏",
                source="某博客",
                url="https://www.zhihu.com/p/1",
                snippet="NAND",
                sourceClass="blog",
                sourceWeight=0.85,
            ),
            NewsItem(
                title="存储芯片涨价 NAND 合约价上修 新华社",
                source="新华社",
                url="https://www.xinhuanet.com/a",
                snippet="NAND",
                sourceClass="official",
                sourceWeight=3.0,
            ),
        ]
        kept = distill_news(items, ["存储", "NAND"], 48)
        self.assertEqual(kept[0].source, "新华社")
        self.assertGreater(kept[0].score, kept[1].score)


class SeriesTests(unittest.TestCase):
    def test_parse_tencent_volume_and_turnover(self) -> None:
        fields = [""] * 40
        fields[1] = "平安银行"
        fields[2] = "000001"
        fields[3] = "12.34"
        fields[6] = "1234567"
        fields[30] = "20260825133157"
        fields[32] = "1.50"
        fields[37] = "987654321"
        body = 'v_sz000001="' + "~".join(fields) + '";'
        rows = parse_tencent_body(body)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].volume or 0, 1234567)
        self.assertAlmostEqual(rows[0].turnover or 0, 987654321)

    def test_yahoo_bars_and_volume_vs_average(self) -> None:
        from src.ingest.quotes import bars_from_yahoo_chart, volume_vs_average

        stamps = [1704067200, 1704153600, 1704240000, 1704326400, 1704412800]
        closes = [10.0, 10.5, 10.2, 10.8, 11.0]
        volumes = [100, 110, 90, 120, 200]
        series = bars_from_yahoo_chart(stamps, closes, volumes, limit=15)
        self.assertEqual(len(series), 5)
        self.assertAlmostEqual(series[1]["changePct"] or 0, 5.0, places=2)
        self.assertAlmostEqual(volume_vs_average(series) or 0, 200 / ((100 + 110 + 90 + 120) / 4), places=3)

    def test_yahoo_intraday_volume_outlier_is_dropped(self) -> None:
        from src.ingest.quotes import bars_from_yahoo_chart, volume_vs_average
        from src.models import QuoteRow
        from src.pulse import build_market_pulse

        stamps = [1704067200 + 86400 * i for i in range(8)]
        closes = [3800.0 + i for i in range(8)]
        volumes = [500000, 520000, 480000, 510000, 490000, 530000, 470000, 3_858_656_340]
        series = bars_from_yahoo_chart(stamps, closes, volumes, limit=15)
        self.assertIsNone(series[-1]["volume"])
        self.assertEqual(series[-2]["volume"], 470000)
        ratio = volume_vs_average(series)
        self.assertIsNotNone(ratio)
        self.assertLess(ratio or 0, 2.0)
        self.assertGreater(ratio or 0, 0.5)

        pulse = build_market_pulse(
            [],
            [QuoteRow(symbol="sh000001", name="上证指数", volumeVsAvg=7422.022, series=series)],
            [],
        )
        self.assertNotIn("7422", pulse["volume"]["note"])
        self.assertNotIn("倍", pulse["volume"]["note"])
        self.assertNotEqual(pulse["volume"]["trend"], "expanding")

    def test_northbound_parser_and_pulse_trends(self) -> None:
        from src.ingest.flows import parse_northbound_rows
        from src.models import QuoteRow
        from src.pulse import build_market_pulse

        payload = {
            "success": True,
            "result": {
                "data": [
                    {
                        "TRADE_DATE": "2026-08-25 00:00:00",
                        "DEAL_AMT": 267421.21,
                        "NET_DEAL_AMT": None,
                        "INDEX_CLOSE_PRICE": 3889.44,
                        "INDEX_CHANGE_RATE": 0.19,
                        "LEAD_STOCKS_NAME": "中南文化",
                    },
                    {
                        "TRADE_DATE": "2026-08-24 00:00:00",
                        "DEAL_AMT": 120000.0,
                        "NET_DEAL_AMT": None,
                        "INDEX_CLOSE_PRICE": 3882.01,
                        "INDEX_CHANGE_RATE": -0.59,
                        "LEAD_STOCKS_NAME": "众合科技",
                    },
                    {
                        "TRADE_DATE": "2026-08-21 00:00:00",
                        "DEAL_AMT": 110000.0,
                        "NET_DEAL_AMT": None,
                        "INDEX_CLOSE_PRICE": 3905.2,
                        "INDEX_CHANGE_RATE": 0.04,
                        "LEAD_STOCKS_NAME": "键凯科技",
                    },
                ]
            },
        }
        rows = parse_northbound_rows(payload, limit=15)
        self.assertEqual([row["date"] for row in rows], ["2026-08-21", "2026-08-24", "2026-08-25"])
        self.assertAlmostEqual(rows[-1]["dealAmtYi"] or 0, 26.74, places=2)
        self.assertIsNone(rows[-1]["netDealAmt"])

        volumes = [100, 100, 100, 100, 100, 100, 100, 220, 230, 240]
        series = [
            {"date": f"2026-08-{day:02d}", "close": 3800 + day, "volume": vol, "changePct": 0.2}
            for day, vol in zip(range(10, 20), volumes)
        ]
        quotes = [
            QuoteRow(
                symbol="sh000001",
                name="上证指数",
                volume=240,
                volumeVsAvg=2.3,
                series=series,
            )
        ]
        news = [
            NewsItem(
                title="存储情绪",
                source="新华社",
                publishedAt=f"2026-08-{day:02d}T04:00:00Z",
                snippet="NAND",
                score=score,
                sourceClass="official" if day >= 18 else "blog",
                sourceWeight=3.0 if day >= 18 else 0.85,
            )
            for day, score in [(12, 2.0), (13, 2.1), (14, 2.0), (18, 8.0), (19, 9.0)]
        ]
        pulse = build_market_pulse(news, quotes, rows)
        self.assertEqual(pulse["volume"]["trend"], "expanding")
        self.assertEqual(pulse["sentiment"]["trend"], "warming")
        self.assertIn("成交额", pulse["northbound"]["note"])
        self.assertFalse(pulse["northbound"]["netBuyAvailable"])


class WeiboHotSearchTests(unittest.TestCase):
    def test_filters_finance_focus_and_stale(self) -> None:
        from datetime import datetime, timezone

        from src.ingest.weibo import parse_hot_rows, rows_from_payload, select_finance_hot

        payload = {
            "ok": 1,
            "data": {
                "band_list": [
                    {
                        "word": "某明星恋情",
                        "category": "艺人",
                        "num": 999999,
                        "realpos": 1,
                        "onboard_time": 1787760000,
                        "label_name": "沸",
                    },
                    {
                        "word": "金价大涨终于熬出头",
                        "category": "财经",
                        "num": 541618,
                        "realpos": 34,
                        "onboard_time": 1787763438,
                        "word_scheme": "#金价大涨终于熬出头#",
                    },
                    {
                        "word": "存储芯片涨价",
                        "category": "数码",
                        "num": 400000,
                        "realpos": 10,
                        "onboard_time": 1787760000,
                    },
                    {
                        "word": "过期财经话题",
                        "category": "财经",
                        "num": 100,
                        "realpos": 50,
                        "onboard_time": 1700000000,
                    },
                    {
                        "word": "A股成交额放大",
                        "category": "",
                        "num": 300000,
                        "realpos": 20,
                        "onboard_time": 1787762000,
                    },
                ]
            },
        }
        rows, source = rows_from_payload(payload)
        self.assertEqual(source, "hot_band")
        parsed = parse_hot_rows(rows, fetched_at="2026-08-27T02:30:00Z")
        now = datetime(2026, 8, 27, 2, 30, tzinfo=timezone.utc)
        kept = select_finance_hot(parsed, ["存储"], max_age_hours=18, limit=16, now=now)
        words = [item.word for item in kept]
        self.assertIn("金价大涨终于熬出头", words)
        self.assertIn("存储芯片涨价", words)
        self.assertIn("A股成交额放大", words)
        self.assertNotIn("某明星恋情", words)
        self.assertNotIn("过期财经话题", words)
        gold = next(item for item in kept if "金价" in item.word)
        self.assertEqual(gold.match, "finance")
        self.assertIn("s.weibo.com/weibo", gold.url)
        storage = next(item for item in kept if "存储" in item.word)
        self.assertEqual(storage.match, "focus")
        market = next(item for item in kept if "A股" in item.word)
        self.assertEqual(market.match, "market")

    def test_skips_entertainment_earnings_and_merges_llm_picks(self) -> None:
        from datetime import datetime, timezone

        from src.ingest.weibo import merge_hot_items, parse_hot_rows

        fetched = "2026-08-27T04:00:00Z"
        parsed = parse_hot_rows(
            [
                {
                    "word": "宇树科技股价跌破600元",
                    "category": "财经",
                    "num": 300000,
                    "realpos": 34,
                    "onboard_time": 1787800000,
                },
                {
                    "word": "陈星旭虞书欣登上稻草熊财报",
                    "category": "艺人",
                    "num": 272000,
                    "realpos": 48,
                    "onboard_time": 1787800000,
                },
                {
                    "word": "苹果发布会定档",
                    "category": "数码",
                    "num": 299000,
                    "realpos": 32,
                    "onboard_time": 1787800000,
                },
                {
                    "word": "何炅自曝断交",
                    "category": "艺人",
                    "num": 400000,
                    "realpos": 12,
                    "onboard_time": 1787800000,
                },
            ],
            fetched_at=fetched,
        )
        now = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)
        kept = merge_hot_items(
            parsed,
            ["存储"],
            ["苹果发布会定档", "陈星旭虞书欣登上稻草熊财报"],
            max_age_hours=24,
            limit=16,
            now=now,
        )
        words = [item.word for item in kept]
        self.assertIn("宇树科技股价跌破600元", words)
        self.assertIn("苹果发布会定档", words)
        self.assertNotIn("陈星旭虞书欣登上稻草熊财报", words)
        self.assertNotIn("何炅自曝断交", words)
        apple = next(item for item in kept if "苹果" in item.word)
        self.assertIn(apple.match, {"llm", "market"})

    def test_drops_lifestyle_noise_even_if_llm_picks(self) -> None:
        from datetime import datetime, timezone

        from src.ingest.weibo import merge_hot_items, parse_hot_rows

        fetched = "2026-08-27T04:00:00Z"
        parsed = parse_hot_rows(
            [
                {
                    "word": "一个爱挤痘痘的人天塌了",
                    "category": "美妆",
                    "num": 800000,
                    "realpos": 2,
                    "onboard_time": 1787800000,
                },
                {
                    "word": "中元节禁忌",
                    "category": "社会",
                    "num": 500000,
                    "realpos": 5,
                    "onboard_time": 1787800000,
                },
                {
                    "word": "酒店国庆标价",
                    "category": "旅游",
                    "num": 420000,
                    "realpos": 8,
                    "onboard_time": 1787800000,
                },
                {
                    "word": "金价大涨终于熬出头",
                    "category": "财经",
                    "num": 300000,
                    "realpos": 34,
                    "onboard_time": 1787800000,
                },
            ],
            fetched_at=fetched,
        )
        now = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)
        kept = merge_hot_items(
            parsed,
            [],
            ["一个爱挤痘痘的人天塌了", "中元节禁忌", "酒店国庆标价", "金价大涨终于熬出头"],
            max_age_hours=24,
            limit=16,
            now=now,
        )
        words = [item.word for item in kept]
        self.assertIn("金价大涨终于熬出头", words)
        self.assertNotIn("一个爱挤痘痘的人天塌了", words)
        self.assertNotIn("中元节禁忌", words)
        self.assertNotIn("酒店国庆标价", words)

    def test_clusters_disaster_hot_search_as_focus_event(self) -> None:
        from datetime import datetime, timezone

        from src.ingest.weibo import event_news_queries, merge_hot_items, parse_hot_rows

        fetched = "2026-08-27T04:00:00Z"
        parsed = parse_hot_rows(
            [
                {
                    "word": "西藏吉隆泥石流已致多人失联",
                    "category": "社会",
                    "num": 1250000,
                    "realpos": 1,
                    "onboard_time": 1787800000,
                },
                {
                    "word": "西藏吉隆县失联人数上升",
                    "category": "社会",
                    "num": 410000,
                    "realpos": 3,
                    "onboard_time": 1787800000,
                },
                {
                    "word": "吉隆堰塞湖持续上涨",
                    "category": "社会",
                    "num": 300000,
                    "realpos": 17,
                    "onboard_time": 1787800000,
                },
                {
                    "word": "应届生减员",
                    "category": "社会",
                    "num": 220000,
                    "realpos": 22,
                    "onboard_time": 1787800000,
                },
            ],
            fetched_at=fetched,
        )
        now = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)
        kept = merge_hot_items(parsed, ["减员"], [], max_age_hours=24, limit=16, now=now)
        mudslide = [item for item in kept if item.cluster == "西藏吉隆泥石流"]
        self.assertGreaterEqual(len(mudslide), 3)
        self.assertTrue(all(item.focusEvent for item in mudslide))
        self.assertTrue(all(item.kind == "event" for item in mudslide))
        self.assertTrue(any(item.word == "应届生减员" for item in kept))
        queries = event_news_queries(kept)
        self.assertTrue(queries)
        self.assertIn("西藏吉隆泥石流", queries[0])

    def test_heuristic_clusters_group_news_by_sector(self) -> None:
        from src.aggregate import heuristic_clusters
        from src.models import HotSearchItem, NewsItem
        from src.planner import heuristic_plan

        plan = heuristic_plan("医药相关", 36)
        news = [
            NewsItem(title="恒瑞医药创新药获批", source="新华社", snippet="创新药"),
            NewsItem(title="迈瑞医疗出口订单", source="中新网", snippet="器械"),
        ]
        hot = [HotSearchItem(word="创新药获批讨论升温", category="财经", match="finance")]
        clusters = heuristic_clusters("医药相关", plan, news, hot)
        self.assertTrue(clusters)
        blob = " ".join(row.name + " " + " ".join(row.newsTitles) for row in clusters)
        self.assertTrue("恒瑞" in blob or "创新药" in blob or "医药" in blob)

    def test_heuristic_keeps_hot_search_out_of_news_limitations(self) -> None:
        from src.models import HotSearchItem
        from src.planner import heuristic_plan
        from src.synthesize import heuristic_report

        plan = heuristic_plan("医药相关", 36)
        hot = [
            HotSearchItem(
                rank=12,
                word="创新药获批讨论升温",
                category="财经",
                fetchedAt="2026-08-25T05:20:00Z",
                onboardAt="2026-08-25T02:10:00Z",
                match="finance",
            )
        ]
        report = heuristic_report(
            focus="医药相关",
            plan=plan,
            news=[],
            quotes=[],
            coverage={
                "news": False,
                "quotes": False,
                "northbound": False,
                "filings": False,
                "x": False,
                "weibo": True,
            },
            errors=[],
            model="heuristic",
            hot_search=hot,
        )
        self.assertEqual(len(report.hotSearch), 1)
        self.assertIn("创新药", report.trendNotes)
        self.assertTrue(any("热搜快照" in item for item in report.limitations))
        self.assertNotIn("未接入微博/X", report.limitations)
        self.assertIn("未接入 X", report.limitations)

    def test_synthesize_with_api_key_compacts_news(self) -> None:
        import os
        from unittest.mock import patch

        from src.models import HotSearchItem, NewsItem
        from src.planner import heuristic_plan
        from src.synthesize import synthesize_report

        plan = heuristic_plan("医药相关", 36)
        news = [
            NewsItem(
                title="创新药获批",
                source="新华社",
                url="https://www.xinhuanet.com/a",
                publishedAt="2026-08-25T04:00:00Z",
                snippet="获批",
                sourceClass="official",
                sourceWeight=3.0,
            )
        ]
        hot = [
            HotSearchItem(
                rank=12,
                word="创新药获批讨论升温",
                category="财经",
                fetchedAt="2026-08-25T05:20:00Z",
                match="finance",
            )
        ]
        coverage = {
            "news": True,
            "quotes": False,
            "northbound": False,
            "filings": False,
            "x": False,
            "weibo": True,
        }
        with patch.dict(os.environ, {"AI_API_KEY": "sk-test"}):
            with patch("src.synthesize.resolve_model", return_value="test-model"):
                with patch(
                    "src.synthesize.chat",
                    return_value='{"crossSectorNotes":"交叉","trendNotes":"时间线"}',
                ):
                    report, model = synthesize_report(
                        focus="医药相关",
                        plan=plan,
                        news=news,
                        quotes=[],
                        coverage=coverage,
                        errors=[],
                        hot_search=hot,
                    )
        self.assertEqual(model, "test-model")
        self.assertEqual(report.crossSectorNotes, "交叉")
        self.assertEqual(len(report.hotSearch), 1)


class OpportunityTests(unittest.TestCase):
    def test_heuristic_maps_disaster_cluster_to_rebuild_names(self) -> None:
        from src.models import HotSearchItem
        from src.opportunities import coerce_opportunities, heuristic_opportunities

        hot = [
            HotSearchItem(
                word="西藏吉隆泥石流已致多人失联",
                cluster="西藏吉隆泥石流",
                clusterHeat=1960000,
                clusterSize=3,
                kind="event",
                focusEvent=True,
                match="event",
                heat=1250000,
            )
        ]
        rows = heuristic_opportunities(hot)
        self.assertGreaterEqual(len(rows), 2)
        names = {row.name for row in rows}
        self.assertIn("海螺水泥", names)
        self.assertTrue(all(row.hotspot == "西藏吉隆泥石流" for row in rows))
        self.assertTrue(all("泥石流" in row.thesis for row in rows))

        coerced = coerce_opportunities(
            [
                {
                    "name": "海螺水泥",
                    "hotspot": "西藏吉隆泥石流",
                    "thesis": "由热搜西藏吉隆泥石流联想到水泥需求，需核对重建规模。",
                    "confidence": 0.4,
                },
                {
                    "symbol": "AAPL",
                    "name": "Apple",
                    "hotspot": "西藏吉隆泥石流",
                    "thesis": "should drop",
                },
            ],
            hot,
            rows,
        )
        self.assertEqual(coerced[0].symbol, "sh600585")
        self.assertTrue(all(row.symbol.startswith(("sh", "sz")) for row in coerced))
        self.assertFalse(any(row.symbol == "AAPL" for row in coerced))


if __name__ == "__main__":
    unittest.main()
