# Changelog

All notable changes to PRISMUX are documented here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) conventions and [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- OpenAI-compatible gateway with both routed (`/v1`) and provider-direct (`/{provider_id}/v1`) endpoints
- Provider adapters for OpenAI-compatible hosts (OpenAI, NVIDIA NIM, Ollama, OpenRouter, Groq, Together AI, Mistral, xAI, LM Studio, custom) and native Anthropic
- Model aliases with ordered fallback routing; qualified (`provider::model`) and raw model resolution
- Per-provider RPM, TPM, burst, concurrency, and timeout controls with token-bucket reservation and reconciliation
- Real SSE streaming with tool-call deltas, reasoning deltas, and translated Anthropic streaming events
- Per-model pricing (per 1M tokens) with automatic cost computation on every request
- Operational dashboard: Overview, Live Feed, History, Test Console, Providers, Routing, Pricing, Storage, Alerts, Access, and an interactive integration Guide
- Supabase Auth-backed authentication with HttpOnly cookies, CSRF protection, and Postgres-authoritative RBAC (Viewer / Operator / Admin)
- Revocable machine API keys (`prismux_live_...`) with HMAC-SHA256 digest storage and scoped access
- Centralized outbound/SSRF policy covering every provider call: DNS pinning, no redirects, permanent cloud-metadata blocking, configurable host/CIDR/port denylists
- PostgreSQL persistence via SQLAlchemy async + asyncpg, with Alembic as the sole schema authority and a private, non-superuser application role
- Self-hosted Supabase stack (Auth, Storage, Studio, PostgREST, postgres-meta) via Docker Compose, with Studio bound to localhost and protected by HTTP Basic Auth
- Configurable retention for request payloads, lightweight stats, and queue samples
