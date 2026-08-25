import unittest
from types import SimpleNamespace

from app.main import provider_cost_breakdown
from app.pricing import estimate_cost


class PricingTests(unittest.TestCase):
    def test_estimates_cost_from_rates_per_million_tokens(self):
        pricing = {"model-a": {"input_per_1m": 0.35, "output_per_1m": 2.75}}

        self.assertAlmostEqual(estimate_cost(pricing, "model-a", 15, 8), 0.00002725)

    def test_provider_pricing_prefers_exact_model_then_default(self):
        app = SimpleNamespace(state=SimpleNamespace(provider_pricing={
            ("provider-a", "default"): {"input_per_1m": 1.0, "output_per_1m": 2.0},
            ("provider-a", "model-a"): {"input_per_1m": 0.35, "output_per_1m": 2.75},
        }))

        exact = provider_cost_breakdown(app, "provider-a", "model-a", 15, 8)
        fallback = provider_cost_breakdown(app, "provider-a", "model-b", 1_000_000, 1_000_000)

        self.assertEqual(exact["priced"], True)
        self.assertAlmostEqual(exact["input"], 0.00000525)
        self.assertAlmostEqual(exact["output"], 0.000022)
        self.assertAlmostEqual(exact["total"], 0.00002725)
        self.assertEqual(fallback, {"priced": True, "input": 1.0, "output": 2.0, "total": 3.0})

    def test_unpriced_requests_remain_unknown(self):
        app = SimpleNamespace(state=SimpleNamespace(provider_pricing={}))

        self.assertEqual(
            provider_cost_breakdown(app, "provider-a", "model-a", 10, 20),
            {"priced": False, "input": None, "output": None, "total": None},
        )


if __name__ == "__main__":
    unittest.main()
