from __future__ import annotations

import unittest

from src.distill import distill_news
from src.ingest.quotes import normalize_symbol, parse_tencent_body
from src.models import NewsItem
from src.planner import heuristic_plan
from src.settings import ai_base_url


class SettingsTests(unittest.TestCase):
    def test_base_url_adds_v1(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"AI_BASE_URL": "https://node-hk.sssaicode.com/api"}):
            self.assertEqual(ai_base_url(), "https://node-hk.sssaicode.com/api/v1")
        with patch.dict(os.environ, {"AI_BASE_URL": "https://node-hk.sssaicode.com/api/v1"}):
            self.assertEqual(ai_base_url(), "https://node-hk.sssaicode.com/api/v1")



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


class PlannerTests(unittest.TestCase):
    def test_storage_preset(self) -> None:
        plan = heuristic_plan("存储相关", 36)
        self.assertTrue(any("存储" in sector for sector in plan.sectors))
        self.assertIn("sh603986", plan.tickers)


if __name__ == "__main__":
    unittest.main()
