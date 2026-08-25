from urllib.parse import urlparse

import httpx


KNOWN_PROVIDERS = (
    ("integrate.api.nvidia.com", "nvidia", "NVIDIA NIM"),
    ("api.openai.com", "openai", "OpenAI"),
    ("openrouter.ai", "openrouter", "OpenRouter"),
    ("api.groq.com", "groq", "Groq"),
    ("api.together.xyz", "together", "Together AI"),
    ("api.mistral.ai", "mistral", "Mistral AI"),
    ("api.x.ai", "xai", "xAI"),
    ("api.anthropic.com", "anthropic", "Anthropic"),
)


def detect_provider(base_url: str) -> dict[str, str]:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    port = parsed.port

    if "ollama" in host or (host in {"localhost", "127.0.0.1", "::1"} and port == 11434):
        return {"id": "ollama", "label": "Ollama"}
    if host in {"localhost", "127.0.0.1", "::1"} and port == 1234:
        return {"id": "lmstudio", "label": "LM Studio"}
    for known_host, provider_id, label in KNOWN_PROVIDERS:
        if host == known_host or host.endswith(f".{known_host}"):
            return {"id": provider_id, "label": label}
    return {"id": "openai-compatible", "label": "OpenAI-compatible"}


def resolve_models_url(base_url: str, explicit_url: str = "") -> str:
    if explicit_url.strip():
        return explicit_url.strip().rstrip("/")

    base_url = base_url.strip().rstrip("/")
    provider = detect_provider(base_url)["id"]
    parsed = urlparse(base_url)
    if provider == "ollama" and not parsed.path.rstrip("/").endswith("/v1"):
        return f"{parsed.scheme}://{parsed.netloc}/api/tags"
    return f"{base_url}/models"


def same_origin(first_url: str, second_url: str) -> bool:
    first = urlparse(first_url)
    second = urlparse(second_url)
    return (
        first.scheme.lower(),
        (first.hostname or "").lower(),
        first.port,
    ) == (
        second.scheme.lower(),
        (second.hostname or "").lower(),
        second.port,
    )


def parse_models(payload: object) -> list[str]:
    candidates: list[object] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            candidates = payload["data"]
        elif isinstance(payload.get("models"), list):
            candidates = payload["models"]
    elif isinstance(payload, list):
        candidates = payload

    models = set()
    for item in candidates:
        if isinstance(item, str):
            models.add(item)
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name") or item.get("model")
            if isinstance(model_id, str) and model_id.strip():
                models.add(model_id.strip())
    return sorted(models, key=str.casefold)


async def discover_models(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str = "",
    models_url: str = "",
) -> dict:
    provider = detect_provider(base_url)
    resolved_url = resolve_models_url(base_url, models_url)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = await client.get(resolved_url, headers=headers, timeout=15)
    response.raise_for_status()
    models = parse_models(response.json())
    if not models:
        raise ValueError("The endpoint responded successfully but returned no recognizable model IDs")
    return {"provider": provider, "models_url": resolved_url, "models": models}
