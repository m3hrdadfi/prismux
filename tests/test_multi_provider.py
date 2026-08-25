import json
import unittest

from app.main import StreamAccumulator, TestRequest as DashboardTestRequest, build_test_payload, extract_test_response_content, is_machine_api_path, provider_scoped_payload
from app.multi_provider import (
    AnthropicAdapter,
    OpenAICompatibleAdapter,
    ProviderConfig,
    ProviderRegistry,
    RouteTarget,
    describe_model_capabilities,
)


def provider_payload(
    provider_id: str,
    base_url: str,
    *,
    default: bool = False,
    adapter: str = "openai-compatible",
) -> dict:
    return {
        "id": provider_id,
        "name": provider_id.title(),
        "preset": "custom",
        "adapter": adapter,
        "base_url": base_url,
        "models_url": "",
        "enabled": True,
        "is_default": default,
        "default_model": "model-one",
        "rate_limit_rpm": 1000,
        "request_burst": 10,
        "tokens_per_minute": None,
        "token_burst": None,
        "max_concurrency": 4,
        "timeout_seconds": 30,
        "anthropic_version": "2023-06-01",
        "headers": {},
        "api_key": "",
        "clear_api_key": False,
    }


class RegistryTests(unittest.TestCase):
    def test_provider_scoped_client_paths_are_machine_authenticated(self):
        self.assertTrue(is_machine_api_path("/v1/chat/completions"))
        self.assertTrue(is_machine_api_path("/v1/models"))
        self.assertTrue(is_machine_api_path("/nvidia/v1/chat/completions"))
        self.assertTrue(is_machine_api_path("/nvidia/v1/models"))
        self.assertFalse(is_machine_api_path("/settings/providers"))

    def test_provider_scoped_payload_bypasses_default_and_routes(self):
        first = ProviderConfig.model_validate(provider_payload("first", "https://first.test/v1", default=True))
        second = ProviderConfig.model_validate(provider_payload("second", "https://second.test/v1"))
        registry = ProviderRegistry([(first, {}), (second, {})], {"quality": [RouteTarget(provider_id="first", model="fallback")]})
        app = type("App", (), {"state": type("State", (), {"registry": registry})()})()
        payload, error = provider_scoped_payload(app, "second", {"model": "quality", "messages": []})
        self.assertIsNone(error)
        self.assertEqual(payload["model"], "second::quality")
        self.assertEqual(registry.resolve(payload["model"])[1][0], RouteTarget(provider_id="second", model="quality"))

        _payload, error = provider_scoped_payload(app, "second", {"model": "first::model-one", "messages": []})
        self.assertEqual(error.status_code, 422)

    def test_model_capabilities_keep_reasoning_controls_provider_aware(self):
        nvidia = ProviderConfig.model_validate({**provider_payload("nvidia", "https://integrate.api.nvidia.com/v1"), "preset": "nvidia"})
        muse = describe_model_capabilities(nvidia, "meta/muse-glimmer-30b")
        qwen = describe_model_capabilities(nvidia, "qwen/qwen3.5-122b")
        self.assertEqual(muse["default_max_tokens"], 8192)
        self.assertEqual(muse["reasoning_modes"], ["auto"])
        self.assertTrue(muse["stream_usage"])
        self.assertEqual(qwen["reasoning_modes"], ["auto", "on", "off"])

    def test_openai_adapter_translates_internal_reasoning_and_usage_controls(self):
        config = ProviderConfig.model_validate(provider_payload("first", "https://first.test/v1", default=True))
        adapter = OpenAICompatibleAdapter(config, "secret")
        _, outgoing = adapter.prepare_request({
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "_reasoning_mode": "off",
            "_include_usage": True,
        }, "qwen/qwen3.5-32b")
        self.assertEqual(outgoing["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(outgoing["stream_options"], {"include_usage": True})
        self.assertNotIn("_reasoning_mode", outgoing)

    def test_dashboard_test_requests_use_model_specific_output_budgets(self):
        config = ProviderConfig.model_validate({**provider_payload("first", "https://first.test/v1", default=True), "preset": "nvidia", "default_model": "meta/muse-glimmer-30b"})
        registry = ProviderRegistry([(config, {})], {})
        app = type("App", (), {"state": type("State", (), {"registry": registry})()})()
        _model, payload = build_test_payload(app, DashboardTestRequest(model="first::meta/muse-glimmer-30b", content="hello"), stream=True)
        self.assertEqual(payload["max_tokens"], 8192)
        self.assertTrue(payload["stream"])
        self.assertTrue(payload["_include_usage"])

    def test_reasoning_only_test_responses_remain_visible(self):
        text, reasoning, finish_reason = extract_test_response_content({
            "choices": [{
                "finish_reason": "length",
                "message": {"content": None, "reasoning_content": "Working through the answer"},
            }],
        })
        self.assertIsNone(text)
        self.assertEqual(reasoning, "Working through the answer")
        self.assertEqual(finish_reason, "length")

    def test_stream_accumulator_preserves_reasoning_answer_usage_and_finish_reason(self):
        accumulator = StreamAccumulator()
        accumulator.consume(b'data: {"id":"chat-1","choices":[{"delta":{"reasoning_content":"Think "},"finish_reason":null}]}\n\n')
        accumulator.consume(b'data: {"choices":[{"delta":{"content":"Answer"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n')
        done, _emitted = accumulator.consume(b"data: [DONE]\n\n")
        usage, estimated = accumulator.usage({"messages": [], "max_tokens": 20})
        payload = accumulator.response_payload("model-one", usage)
        self.assertTrue(done)
        self.assertFalse(estimated)
        self.assertEqual(payload["choices"][0]["message"]["reasoning_content"], "Think ")
        self.assertEqual(payload["choices"][0]["message"]["content"], "Answer")
        self.assertEqual(payload["choices"][0]["finish_reason"], "stop")
        self.assertEqual(payload["usage"]["total_tokens"], 5)

    def test_resolves_alias_qualified_and_raw_models(self):
        first = ProviderConfig.model_validate(provider_payload("first", "https://first.test/v1", default=True))
        second = ProviderConfig.model_validate(provider_payload("second", "https://second.test/v1"))
        registry = ProviderRegistry(
            [(first, {}), (second, {})],
            {
                "quality": [
                    RouteTarget(provider_id="second", model="best"),
                    RouteTarget(provider_id="first", model="fallback"),
                ]
            },
        )
        self.assertEqual([target.provider_id for target in registry.resolve("quality")[1]], ["second", "first"])
        self.assertEqual(registry.resolve("second::direct")[1][0].model, "direct")
        self.assertEqual(registry.resolve("raw-model")[1][0].provider_id, "first")

    def test_provider_health_tracks_request_outcomes(self):
        config = ProviderConfig.model_validate(provider_payload("first", "https://first.test/v1", default=True))
        runtime = ProviderRegistry([(config, {})], {}).default

        runtime.observe_status(200)
        self.assertEqual(runtime.health, "healthy")
        runtime.observe_status(503)
        self.assertEqual(runtime.health, "degraded")
        runtime.observe_status(401)
        self.assertEqual(runtime.health, "auth_error")
        runtime.mark_health("offline", "ConnectError")
        self.assertEqual((runtime.health, runtime.last_health_error), ("offline", "ConnectError"))

    def test_registry_reload_preserves_existing_health_without_initializing_new_provider(self):
        first = ProviderConfig.model_validate(provider_payload("first", "https://first.test/v1", default=True))
        previous = ProviderRegistry([(first, {})], {})
        previous.default.mark_health("healthy")
        checked_at = previous.default.last_checked_at

        second = ProviderConfig.model_validate(provider_payload("second", "https://second.test/v1"))
        reloaded = ProviderRegistry([(first, {}), (second, {})], {})
        reloaded.inherit_health(previous)

        self.assertEqual(reloaded.providers["first"].health, "healthy")
        self.assertEqual(reloaded.providers["first"].last_checked_at, checked_at)
        self.assertEqual(reloaded.providers["second"].health, "unknown")
        self.assertIsNone(reloaded.providers["second"].last_checked_at)


class AnthropicAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_translates_tools_responses_and_streams(self):
        config = ProviderConfig.model_validate(
            provider_payload("claude", "https://api.anthropic.com/v1", adapter="anthropic")
        )
        adapter = AnthropicAdapter(config, "secret")
        url, request = adapter.prepare_request(
            {
                "messages": [
                    {"role": "system", "content": "Be concise"},
                    {"role": "user", "content": "Weather?"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "weather", "parameters": {"type": "object"}},
                    }
                ],
                "tool_choice": "required",
                "max_tokens": 50,
            },
            "claude-test",
        )
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request["system"][0]["text"], "Be concise")
        self.assertEqual(request["tools"][0]["name"], "weather")
        normalized = adapter.normalize_response(
            {
                "id": "msg_1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "weather",
                        "input": {"city": "Stockholm"},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
            "claude-test",
        )
        self.assertEqual(normalized["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(normalized["usage"]["total_tokens"], 20)

        async def lines():
            events = [
                {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 3}}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}"

        chunks = [chunk async for chunk in adapter.translate_stream(lines(), "claude-test")]
        self.assertTrue(any(b"Hello" in chunk for chunk in chunks))
        self.assertTrue(any(b'"total_tokens": 4' in chunk for chunk in chunks))
        self.assertEqual(chunks[-1], b"data: [DONE]\n\n")


if __name__ == "__main__":
    unittest.main()
