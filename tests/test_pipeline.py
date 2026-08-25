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


if __name__ == "__main__":
    unittest.main()
