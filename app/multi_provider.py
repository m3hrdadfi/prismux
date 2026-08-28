from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field, field_validator, model_validator

from app.limiter import TokenBucket
from app.providers import detect_provider, parse_models, resolve_models_url, same_origin


RESERVED_HEADERS = {"host", "content-length", "authorization", "x-api-key"}
FALLBACK_STATUS_CODES = {408, 429, 500, 502, 503, 504, 529}

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {"name": "OpenAI", "adapter": "openai-compatible", "base_url": "https://api.openai.com/v1"},
    "nvidia": {"name": "NVIDIA NIM", "adapter": "openai-compatible", "base_url": "https://integrate.api.nvidia.com/v1"},
    "anthropic": {"name": "Anthropic", "adapter": "anthropic", "base_url": "https://api.anthropic.com/v1"},
    "ollama": {
        "name": "Ollama",
        "adapter": "openai-compatible",
        "base_url": "http://localhost:11434/v1",
        "models_url": "http://localhost:11434/v1/models",
    },
    "openrouter": {"name": "OpenRouter", "adapter": "openai-compatible", "base_url": "https://openrouter.ai/api/v1"},
    "groq": {"name": "Groq", "adapter": "openai-compatible", "base_url": "https://api.groq.com/openai/v1"},
    "together": {"name": "Together AI", "adapter": "openai-compatible", "base_url": "https://api.together.xyz/v1"},
    "mistral": {"name": "Mistral AI", "adapter": "openai-compatible", "base_url": "https://api.mistral.ai/v1"},
    "xai": {"name": "xAI", "adapter": "openai-compatible", "base_url": "https://api.x.ai/v1"},
    "lmstudio": {"name": "LM Studio", "adapter": "openai-compatible", "base_url": "http://localhost:1234/v1"},
    "custom": {"name": "Custom provider", "adapter": "openai-compatible", "base_url": "http://localhost:8000/v1"},
}


class ProviderConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    preset: str = "custom"
    adapter: Literal["openai-compatible", "anthropic"] = "openai-compatible"
    base_url: str
    models_url: str = ""
    enabled: bool = True
    is_default: bool = False
    default_model: str = ""
    rate_limit_rpm: float | None = Field(default=40, gt=0, le=1_000_000)
    request_burst: float = Field(default=1, gt=0, le=1_000_000)
    tokens_per_minute: float | None = Field(default=None, gt=0, le=1_000_000_000)
    token_burst: float | None = Field(default=None, gt=0, le=1_000_000_000)
    max_concurrency: int | None = Field(default=None, gt=0, le=100_000)
    timeout_seconds: float = Field(default=120, gt=0, le=3600)
    anthropic_version: str = "2023-06-01"
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url", "models_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("URLs must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def validate_models_origin(self) -> "ProviderConfig":
        if self.models_url and not same_origin(self.base_url, self.models_url):
            raise ValueError("Models URL must use the same origin as the provider base URL")
        return self

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for name, header_value in value.items():
            normalized = name.strip()
            if not normalized or normalized.lower() in RESERVED_HEADERS:
                raise ValueError(f"Header '{name}' is reserved or invalid")
            cleaned[normalized] = str(header_value)
        return cleaned


class RouteTarget(BaseModel):
    provider_id: str
    model: str = Field(min_length=1)


class CredentialCipher:
    def __init__(self, key: str):
        self._fernet: Fernet | None = None
        self.error: str | None = None
        if key.strip():
            try:
                self._fernet = Fernet(key.strip().encode())
            except (ValueError, TypeError):
                self.error = "SETTINGS_ENCRYPTION_KEY is not a valid Fernet key"

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: dict[str, Any]) -> str:
        if not self._fernet:
            raise ValueError(self.error or "SETTINGS_ENCRYPTION_KEY is required before secrets can be saved")
        return self._fernet.encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        if not token:
            return {}
        if not self._fernet:
            raise ValueError("SETTINGS_ENCRYPTION_KEY is required to read encrypted provider credentials")
        try:
            value = json.loads(self._fernet.decrypt(token.encode()))
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Provider credentials could not be decrypted with SETTINGS_ENCRYPTION_KEY") from exc
        return value if isinstance(value, dict) else {}


class CapacityController:
    def __init__(self, config: ProviderConfig):
        self.request_bucket = (
            TokenBucket(config.rate_limit_rpm, config.request_burst) if config.rate_limit_rpm else None
        )
        token_capacity = config.token_burst or config.tokens_per_minute
        self.token_bucket = (
            TokenBucket(config.tokens_per_minute, token_capacity)
            if config.tokens_per_minute and token_capacity
            else None
        )
        self.semaphore = asyncio.Semaphore(config.max_concurrency) if config.max_concurrency else None
        self._queued = 0
        self._active = 0
        self._state_lock = asyncio.Lock()

    async def acquire(self, token_reservation: int) -> tuple[int, int]:
        started = time.monotonic()
        async with self._state_lock:
            self._queued += 1
        try:
            if self.request_bucket:
                await self.request_bucket.acquire()
            if self.token_bucket:
                remaining = max(1, token_reservation)
                while remaining:
                    chunk = min(remaining, int(self.token_bucket.capacity))
                    await self.token_bucket.acquire(chunk)
                    remaining -= chunk
            if self.semaphore:
                await self.semaphore.acquire()
            async with self._state_lock:
                self._active += 1
        finally:
            async with self._state_lock:
                self._queued -= 1
        return round((time.monotonic() - started) * 1000), token_reservation

    async def release(self, reserved: int, actual: int | None) -> None:
        if self.token_bucket and actual is not None and actual < reserved:
            await self.token_bucket.refund(reserved - actual)
        if self.semaphore:
            self.semaphore.release()
        async with self._state_lock:
            self._active = max(0, self._active - 1)

    async def snapshot(self) -> dict[str, Any]:
        request_level = await self.request_bucket.level() if self.request_bucket else None
        token_level = await self.token_bucket.level() if self.token_bucket else None
        async with self._state_lock:
            return {"queued": self._queued, "active": self._active, "request_level": request_level, "token_level": token_level}


def estimate_requested_tokens(payload: dict[str, Any]) -> int:
    text_size = 0
    for message in payload.get("messages", []):
        if not isinstance(message, dict):
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            text_size += len(content)
        elif isinstance(content, list):
            text_size += sum(len(str(part.get("text", ""))) for part in content if isinstance(part, dict))
    requested = payload.get("max_tokens") or payload.get("max_completion_tokens") or 0
    return max(1, (text_size + 3) // 4 + int(requested or 0))


def estimate_embedding_tokens(payload: dict[str, Any]) -> int:
    text_input = payload.get("input", "")
    if isinstance(text_input, str):
        text_size = len(text_input)
    elif isinstance(text_input, list):
        text_size = sum(len(item) for item in text_input if isinstance(item, str))
    else:
        text_size = 0
    return max(1, (text_size + 3) // 4)


def describe_model_capabilities(config: ProviderConfig, model: str) -> dict[str, Any]:
    """Describe only reasoning controls that are known to be safe for a model family."""
    normalized = model.strip().lower()
    capability: dict[str, Any] = {
        "streaming": True,
        "stream_usage": config.adapter == "anthropic" or config.preset in {"openai", "nvidia", "ollama", "openrouter", "groq", "together", "mistral", "xai"},
        "reasoning_modes": ["auto"],
        "default_max_tokens": 512,
        "reasoning_control": None,
    }
    if "muse-glimmer" in normalized:
        # NVIDIA's reference request uses 8192. The model exposes separate
        # reasoning output but does not document a portable on/off control.
        capability["default_max_tokens"] = 8192
        return capability
    if config.adapter == "anthropic":
        capability["default_max_tokens"] = 4096
        return capability
    if "nemotron-3-super" in normalized:
        capability.update({
            "reasoning_modes": ["auto", "on", "off"],
            "default_max_tokens": 8192,
            "reasoning_control": "reasoning_effort",
        })
    elif any(name in normalized for name in ("qwen3", "qwen-3", "glm4", "glm-4", "nemotron-3-ultra", "nemotron-3-nano")):
        capability.update({
            "reasoning_modes": ["auto", "on", "off"],
            "default_max_tokens": 4096,
            "reasoning_control": "enable_thinking",
        })
    elif "kimi-k2" in normalized:
        capability.update({
            "reasoning_modes": ["auto", "off"],
            "default_max_tokens": 4096,
            "reasoning_control": "nvidia_thinking" if config.preset == "nvidia" else "template_thinking",
        })
    return capability


def apply_reasoning_mode(payload: dict[str, Any], config: ProviderConfig, model: str, mode: str) -> None:
    if mode == "auto":
        return
    capability = describe_model_capabilities(config, model)
    if mode not in capability["reasoning_modes"]:
        raise AdapterError(f"Model '{model}' does not expose a supported reasoning {mode} control")
    control = capability["reasoning_control"]
    if control == "reasoning_effort":
        payload["reasoning_effort"] = "high" if mode == "on" else "none"
    elif control == "enable_thinking":
        template = dict(payload.get("chat_template_kwargs") or {})
        template["enable_thinking"] = mode == "on"
        payload["chat_template_kwargs"] = template
    elif control == "template_thinking":
        template = dict(payload.get("chat_template_kwargs") or {})
        template["thinking"] = mode == "on"
        payload["chat_template_kwargs"] = template
    elif control == "nvidia_thinking":
        payload["thinking"] = {"type": "enabled" if mode == "on" else "disabled"}


class AdapterError(ValueError):
    pass


class ProviderAdapter:
    def __init__(self, config: ProviderConfig, api_key: str, secret_headers: dict[str, str] | None = None):
        self.config = config
        self.api_key = api_key
        self.secret_headers = secret_headers or {}

    def headers(self) -> dict[str, str]:
        raise NotImplementedError

    def prepare_request(self, payload: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    def prepare_embeddings_request(self, payload: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
        raise AdapterError(f"Provider adapter '{self.config.adapter}' does not support embeddings")

    async def discover_models(self, client: httpx.AsyncClient) -> list[str]:
        url = resolve_models_url(self.config.base_url, self.config.models_url)
        response = await client.get(url, headers=self.headers(), timeout=min(self.config.timeout_seconds, 30))
        response.raise_for_status()
        models = parse_models(response.json())
        if not models:
            raise ValueError("The endpoint returned no recognizable model IDs")
        return models

    def normalize_response(self, response: dict[str, Any], model: str) -> dict[str, Any]:
        return response

    async def translate_stream(self, lines: AsyncIterator[str], model: str) -> AsyncIterator[bytes]:
        async for line in lines:
            yield f"{line}\n".encode()


class OpenAICompatibleAdapter(ProviderAdapter):
    def headers(self) -> dict[str, str]:
        return {
            **self.config.headers,
            **self.secret_headers,
            **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    def prepare_request(self, payload: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
        outgoing = dict(payload)
        reasoning_mode = str(outgoing.pop("_reasoning_mode", "auto"))
        include_usage = bool(outgoing.pop("_include_usage", False))
        apply_reasoning_mode(outgoing, self.config, model, reasoning_mode)
        if include_usage and outgoing.get("stream"):
            stream_options = dict(outgoing.get("stream_options") or {})
            stream_options["include_usage"] = True
            outgoing["stream_options"] = stream_options
        outgoing["model"] = model
        return f"{self.config.base_url}/chat/completions", outgoing

    def prepare_embeddings_request(self, payload: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
        if "input" not in payload:
            raise AdapterError("Embeddings requests require an 'input' field")
        outgoing = dict(payload)
        outgoing["model"] = model
        return f"{self.config.base_url}/embeddings", outgoing


def _anthropic_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content)}]
    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in {"text", "input_text"}:
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif part_type == "image_url":
            image = part.get("image_url", {})
            url = image.get("url", "") if isinstance(image, dict) else str(image)
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(",", 1)
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": header[5:].split(";")[0], "data": data}})
            elif url.startswith(("http://", "https://")):
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                raise AdapterError("Anthropic image content requires an HTTP URL or base64 data URL")
        else:
            raise AdapterError(f"Anthropic does not support content part type '{part_type}' through this proxy")
    return blocks


class AnthropicAdapter(ProviderAdapter):
    UNSUPPORTED_FIELDS = {"response_format", "audio", "modalities", "prediction", "service_tier"}

    def headers(self) -> dict[str, str]:
        return {
            **self.config.headers,
            **self.secret_headers,
            **({"x-api-key": self.api_key} if self.api_key else {}),
            "anthropic-version": self.config.anthropic_version,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    def prepare_request(self, payload: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
        reasoning_mode = str(payload.get("_reasoning_mode", "auto"))
        if reasoning_mode != "auto":
            raise AdapterError("Anthropic reasoning controls are not enabled for this adapter")
        unsupported = sorted(field for field in self.UNSUPPORTED_FIELDS if field in payload)
        if unsupported:
            raise AdapterError(f"Anthropic adapter does not support: {', '.join(unsupported)}")
        system: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = []
        for message in payload.get("messages", []):
            if not isinstance(message, dict):
                raise AdapterError("Every message must be an object")
            role = message.get("role")
            if role in {"system", "developer"}:
                system.extend(_anthropic_content(message.get("content", "")))
                continue
            if role == "tool":
                messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": message.get("tool_call_id", ""), "content": message.get("content", "")} ]})
                continue
            if role not in {"user", "assistant"}:
                raise AdapterError(f"Anthropic adapter does not support message role '{role}'")
            content = _anthropic_content(message.get("content", ""))
            if role == "assistant":
                for call in message.get("tool_calls", []) or []:
                    function = call.get("function", {}) if isinstance(call, dict) else {}
                    try:
                        arguments = json.loads(function.get("arguments", "{}"))
                    except json.JSONDecodeError as exc:
                        raise AdapterError("Tool call arguments must contain valid JSON") from exc
                    content.append({"type": "tool_use", "id": call.get("id", ""), "name": function.get("name", ""), "input": arguments})
            messages.append({"role": role, "content": content})
        outgoing: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": payload.get("max_tokens") or payload.get("max_completion_tokens") or 1024,
            "stream": bool(payload.get("stream")),
        }
        if system:
            outgoing["system"] = system
        for field in ("temperature", "top_p", "top_k", "stop_sequences"):
            source = "stop" if field == "stop_sequences" else field
            if source in payload:
                outgoing[field] = payload[source]
        if payload.get("tools"):
            outgoing["tools"] = [
                {"name": tool.get("function", {}).get("name", ""), "description": tool.get("function", {}).get("description", ""), "input_schema": tool.get("function", {}).get("parameters", {"type": "object", "properties": {}})}
                for tool in payload["tools"] if isinstance(tool, dict) and tool.get("type", "function") == "function"
            ]
        choice = payload.get("tool_choice")
        if choice == "required":
            outgoing["tool_choice"] = {"type": "any"}
        elif choice == "auto":
            outgoing["tool_choice"] = {"type": "auto"}
        elif isinstance(choice, dict) and choice.get("function", {}).get("name"):
            outgoing["tool_choice"] = {"type": "tool", "name": choice["function"]["name"]}
        return f"{self.config.base_url}/messages", outgoing

    def normalize_response(self, response: dict[str, Any], model: str) -> dict[str, Any]:
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({"id": block.get("id", ""), "type": "function", "function": {"name": block.get("name", ""), "arguments": json.dumps(block.get("input", {}), separators=(",", ":"))}})
        usage = response.get("usage", {})
        prompt = usage.get("input_tokens")
        completion = usage.get("output_tokens")
        finish_map = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls", "stop_sequence": "stop"}
        message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return {
            "id": response.get("id", ""), "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_map.get(response.get("stop_reason"), response.get("stop_reason"))}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": (prompt + completion) if isinstance(prompt, int) and isinstance(completion, int) else None},
        }

    async def discover_models(self, client: httpx.AsyncClient) -> list[str]:
        url = self.config.models_url or f"{self.config.base_url}/models"
        response = await client.get(url, headers=self.headers(), timeout=min(self.config.timeout_seconds, 30))
        response.raise_for_status()
        return parse_models(response.json())

    async def translate_stream(self, lines: AsyncIterator[str], model: str) -> AsyncIterator[bytes]:
        message_id = ""
        input_tokens: int | None = None
        tool_indexes: dict[int, int] = {}
        async for line in lines:
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            event = json.loads(data)
            event_type = event.get("type")
            if event_type == "message_start":
                message_id = event.get("message", {}).get("id", "")
                usage = event.get("message", {}).get("usage", {})
                input_tokens = usage.get("input_tokens") if isinstance(usage.get("input_tokens"), int) else None
                chunk = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}], "usage": {"prompt_tokens": input_tokens}}
                yield f"data: {json.dumps(chunk)}\n\n".encode()
            elif event_type == "content_block_start" and event.get("content_block", {}).get("type") == "tool_use":
                index = int(event.get("index", 0)); tool_indexes[index] = len(tool_indexes)
                block = event["content_block"]
                delta = {"tool_calls": [{"index": tool_indexes[index], "id": block.get("id", ""), "type": "function", "function": {"name": block.get("name", ""), "arguments": ""}}]}
                chunk = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                yield f"data: {json.dumps(chunk)}\n\n".encode()
            elif event_type == "content_block_delta":
                delta_event = event.get("delta", {})
                if delta_event.get("type") == "text_delta":
                    delta = {"content": delta_event.get("text", "")}
                elif delta_event.get("type") == "thinking_delta":
                    delta = {"reasoning_content": delta_event.get("thinking", "")}
                elif delta_event.get("type") == "input_json_delta":
                    idx = tool_indexes.get(int(event.get("index", 0)), 0)
                    delta = {"tool_calls": [{"index": idx, "function": {"arguments": delta_event.get("partial_json", "")}}]}
                else:
                    continue
                chunk = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                yield f"data: {json.dumps(chunk)}\n\n".encode()
            elif event_type == "message_delta":
                reason = event.get("delta", {}).get("stop_reason")
                finish = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls", "stop_sequence": "stop"}.get(reason, reason)
                usage = event.get("usage", {})
                output_tokens = usage.get("output_tokens")
                total_tokens = input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None
                chunk = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "total_tokens": total_tokens}}
                yield f"data: {json.dumps(chunk)}\n\n".encode()
            elif event_type == "error":
                yield f"data: {json.dumps({'error': event.get('error', {})})}\n\n".encode()
        yield b"data: [DONE]\n\n"


def make_adapter(config: ProviderConfig, api_key: str, secret_headers: dict[str, str] | None = None) -> ProviderAdapter:
    if config.adapter == "anthropic":
        return AnthropicAdapter(config, api_key, secret_headers)
    return OpenAICompatibleAdapter(config, api_key, secret_headers)


@dataclass
class ProviderRuntime:
    config: ProviderConfig
    adapter: ProviderAdapter
    capacity: CapacityController
    api_key_configured: bool
    health: str = "unknown"
    last_health_error: str = ""
    last_checked_at: float | None = None

    def mark_health(self, health: str, error: str = "") -> None:
        self.health = health
        self.last_health_error = error
        self.last_checked_at = time.time()

    def observe_status(self, status_code: int) -> None:
        if status_code in {401, 403}:
            self.mark_health("auth_error", f"HTTP {status_code}")
        elif status_code in FALLBACK_STATUS_CODES or status_code >= 500:
            self.mark_health("degraded", f"HTTP {status_code}")
        else:
            # A non-authentication client error still proves that the provider
            # is reachable and responding; it should not look offline.
            self.mark_health("healthy")


class ProviderRegistry:
    def __init__(self, providers: list[tuple[ProviderConfig, dict[str, Any]]], routes: dict[str, list[RouteTarget]]):
        self.providers = {
            config.id: ProviderRuntime(
                config,
                make_adapter(config, str(credentials.get("api_key", "")), credentials.get("secret_headers", {})),
                CapacityController(config),
                bool(credentials.get("api_key")),
            )
            for config, credentials in providers
        }
        self.routes = routes

    def inherit_health(self, previous: "ProviderRegistry | None") -> None:
        """Preserve observed health when configuration reloads recreate runtimes."""
        if previous is None:
            return
        for provider_id, runtime in self.providers.items():
            prior = previous.providers.get(provider_id)
            if prior is None:
                continue
            runtime.health = prior.health
            runtime.last_health_error = prior.last_health_error
            runtime.last_checked_at = prior.last_checked_at

    @property
    def default(self) -> ProviderRuntime:
        provider = next((item for item in self.providers.values() if item.config.is_default), None)
        if provider is None:
            raise ValueError("No default provider is configured")
        return provider

    def resolve(self, requested_model: str | None) -> tuple[str, list[RouteTarget]]:
        selector = (requested_model or self.default.config.default_model).strip()
        if not selector:
            raise ValueError("No model was supplied and the default provider has no default model")
        if selector in self.routes:
            return selector, self.routes[selector]
        if "::" in selector:
            provider_id, model = selector.split("::", 1)
            if not provider_id or not model:
                raise ValueError("Qualified models must use provider_id::model_id")
            return selector, [RouteTarget(provider_id=provider_id, model=model)]
        return selector, [RouteTarget(provider_id=self.default.config.id, model=selector)]

    def enabled_targets(self, targets: list[RouteTarget]) -> list[tuple[ProviderRuntime, str]]:
        resolved = []
        for target in targets:
            runtime = self.providers.get(target.provider_id)
            if runtime and runtime.config.enabled:
                resolved.append((runtime, target.model))
        return resolved


def provider_from_legacy(values: dict[str, Any]) -> ProviderConfig:
    detected = detect_provider(str(values.get("base_url", "")))
    provider_id = detected["id"] if detected["id"] != "openai-compatible" else "default"
    adapter = "anthropic" if detected["id"] == "anthropic" else "openai-compatible"
    return ProviderConfig(
        id=provider_id,
        name=detected["label"],
        preset=detected["id"],
        adapter=adapter,
        base_url=values["base_url"],
        models_url=values.get("models_url", ""),
        default_model=values.get("default_model", ""),
        rate_limit_rpm=values.get("rate_limit_rpm", 40),
        request_burst=values.get("bucket_capacity", 1),
        is_default=True,
    )


def provider_public(runtime: ProviderRuntime, models: list[str] | None = None, models_updated_at: str | None = None) -> dict[str, Any]:
    available_models = models or []
    data = runtime.config.model_dump()
    data.update({
        "api_key_configured": runtime.api_key_configured,
        "health": runtime.health,
        "health_error": runtime.last_health_error,
        "last_checked_at": runtime.last_checked_at,
        "models": available_models,
        "model_capabilities": {
            model: describe_model_capabilities(runtime.config, model)
            for model in dict.fromkeys([runtime.config.default_model, *available_models])
            if model
        },
        "models_updated_at": models_updated_at,
    })
    return data
