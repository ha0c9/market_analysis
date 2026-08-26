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
        self.assertLessEqual(len(plan.newsQueries), 7)
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


if __name__ == "__main__":
    unittest.main()
