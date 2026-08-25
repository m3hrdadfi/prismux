import unittest

import httpx

from app.config import RuntimeSettings, settings
from app.multi_provider import PROVIDER_PRESETS
from app.providers import detect_provider, discover_models, parse_models, resolve_models_url, same_origin


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_detects_known_and_local_providers(self):
        self.assertEqual(detect_provider("https://integrate.api.nvidia.com/v1")["id"], "nvidia")
        self.assertEqual(detect_provider("http://localhost:11434")["id"], "ollama")
        self.assertEqual(detect_provider("http://localhost:1234/v1")["id"], "lmstudio")
        self.assertEqual(detect_provider("https://models.internal/v1")["id"], "openai-compatible")

    def test_resolves_standard_and_ollama_model_urls(self):
        self.assertEqual(resolve_models_url("https://api.openai.com/v1"), "https://api.openai.com/v1/models")
        self.assertEqual(resolve_models_url("http://localhost:11434"), "http://localhost:11434/api/tags")
        self.assertEqual(resolve_models_url("http://localhost:11434/v1"), "http://localhost:11434/v1/models")
        self.assertEqual(PROVIDER_PRESETS["ollama"]["models_url"], "http://localhost:11434/v1/models")

    def test_saved_credentials_can_only_be_reused_on_the_same_origin(self):
        self.assertTrue(same_origin("https://api.openai.com/v1/models", "https://api.openai.com/v1"))
        self.assertFalse(same_origin("https://example.com/models", "https://api.openai.com/v1"))
        self.assertFalse(same_origin("http://api.openai.com/models", "https://api.openai.com/v1"))

    def test_parses_openai_and_ollama_payloads(self):
        self.assertEqual(parse_models({"data": [{"id": "gpt-b"}, {"id": "gpt-a"}]}), ["gpt-a", "gpt-b"])
        self.assertEqual(parse_models({"models": [{"name": "qwen3:8b"}]}), ["qwen3:8b"])

    async def test_discovers_models_with_bearer_auth(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer secret-key")
            return httpx.Response(200, json={"data": [{"id": "model-one"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover_models(client, base_url="https://api.openai.com/v1", api_key="secret-key")
        self.assertEqual(result["models"], ["model-one"])
        self.assertEqual(result["provider"]["id"], "openai")


class RuntimeSettingsTests(unittest.TestCase):
    def test_database_values_override_environment_defaults(self):
        runtime = RuntimeSettings.from_sources(
            settings,
            {"rate_limit_rpm": 13, "base_url": "https://api.openai.com/v1"},
        )
        self.assertEqual(runtime.rate_limit_rpm, 13)
        self.assertEqual(runtime.base_url, "https://api.openai.com/v1")


if __name__ == "__main__":
    unittest.main()
