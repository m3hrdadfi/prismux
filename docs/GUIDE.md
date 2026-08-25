# PRISMUX Guide

This is the getting-started guide for running PRISMUX and connecting clients to it. For hardening a public deployment, see [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md). For contributing to the project, see [../CONTRIBUTING.md](../CONTRIBUTING.md).

## What PRISMUX is

PRISMUX is a self-hosted gateway that sits in front of one or more AI providers (OpenAI, Anthropic, NVIDIA NIM, Ollama, OpenRouter, Groq, Together AI, Mistral, xAI, LM Studio, or any OpenAI-compatible host) and exposes them behind a single OpenAI-compatible API. It adds the operational layer providers don't give you on their own: per-provider rate limits, ordered fallback routing, usage and cost accounting, request history, health checks, and a dashboard to manage all of it.

## Quickstart

```sh
cp .env.example .env
docker compose up --build
```

This starts PostgreSQL, Supabase Auth, the Alembic migration, and the FastAPI application. On first boot, a bootstrap admin account is created from `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` in `.env`.

Open the dashboard:

```text
http://localhost:8100
```

Log in with the bootstrap admin credentials, then:

1. **Add a provider** under **Settings → Providers** — base URL, credentials, and capacity limits (RPM, TPM, concurrency, timeout).
2. **Discover its models** so PRISMUX caches what's available upstream.
3. **Create a machine API key** under **Settings → Access → Machine API keys**. This is what client applications authenticate with — never with a raw provider credential. The full key is shown once; copy it immediately.
4. **Send a test request** from **Test Console**, or connect a real client (see below).

Every step above is also walked through interactively in the dashboard's own **Guide** page (`/guide`), which fills in your actual provider IDs and models as you configure them.

## Dashboard tour

| Page | Purpose |
|---|---|
| Overview | Provider health, capacity, queues, traffic, latency, tokens, cost, and error trends |
| Live Feed | Live-polling request stream with pause/resume and expandable detail |
| History | Filterable, paginated request history with JSON/CSV export |
| Test Console | Ad-hoc requests with streaming, reasoning controls, and raw payload inspection |
| Settings → Providers | Connection, credentials, capacity, and health per provider |
| Settings → Routing | Aliases and ordered fallback targets |
| Settings → Pricing | Per-provider, per-model rates (per 1M tokens) used for cost accounting |
| Settings → Storage | Retention windows for payloads, stats, and queue samples |
| Settings → Alerts | Thresholds for the Overview banners |
| Settings → Access | Users, roles, machine keys, audit log (admin only) |

## Connecting a client

PRISMUX exposes two request paths:

- **Routed** — `http://localhost:8100/v1` — resolves aliases (with fallback), qualified `provider_id::model_id` selectors, or the default provider for a raw model ID.
- **Direct** — `http://localhost:8100/{provider_id}/v1` — always targets that one provider. Use the provider's own raw model ID here, not a qualified one.

Any OpenAI-compatible SDK works unmodified — point it at one of the base URLs above and use your PRISMUX machine key (`prismux_live_...`) as the API key:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8100/v1",
    api_key="prismux_live_your_key",
)

response = client.chat.completions.create(
    model="nvidia::meta/llama-3.1-70b-instruct",
    messages=[{"role": "user", "content": "Hello"}],
)
```

Streaming works the same way with `stream=True`. See the in-app **Guide** page for copy-paste examples pre-filled with your configured providers, including Hermes Agent's custom-endpoint config format.

## Where to go next

- Hardening a deployment beyond localhost: [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)
- Filing an issue or opening a PR: [../CONTRIBUTING.md](../CONTRIBUTING.md)
- What changed between releases: [../CHANGELOG.md](../CHANGELOG.md)
