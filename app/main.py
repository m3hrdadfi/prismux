import asyncio
import csv
import hashlib
import io
import json
import secrets
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app import db
from app.auth import (
    AuthenticationError,
    AuthorizationError,
    SecurityService,
    SupabaseAuthClient,
    authorize_role,
    new_csrf_token,
    new_key_id,
    required_role,
)
from app.chart_ranges import DEFAULT_RANGE, RANGE_PRESETS, resolve_range
from app.config import RuntimeSettings, settings
from app.limiter import TokenBucket
from app.multi_provider import (
    AdapterError,
    CredentialCipher,
    FALLBACK_STATUS_CODES,
    PROVIDER_PRESETS,
    RESERVED_HEADERS,
    ProviderConfig,
    ProviderRegistry,
    RouteTarget,
    describe_model_capabilities,
    estimate_requested_tokens,
    make_adapter,
    provider_from_legacy,
    provider_public,
)
from app.outbound import OutboundPolicy, OutboundPolicyError, ProtectedAsyncClient
from app.pricing import estimate_cost
from app.providers import detect_provider, discover_models, resolve_models_url, same_origin
from app.queue_state import QueueState
from app.sanitize import sanitize

PREVIEW_LENGTH = 80
RECENT_WINDOW_SECONDS = 60
TREND_WINDOW_SECONDS = 3600  # "in the last hour" context on the Queued / Avg Wait cards
ROLLING_AVG_WAIT_N = 20
PURGE_INTERVAL_SECONDS = 300
STATE_SAMPLE_INTERVAL_SECONDS = 1
PAGE_SIZE = 50
COST_SPARKLINE_HOURS = 24
ALERT_ERROR_RATE_WINDOW_SECONDS = 300  # 5 minutes
ALERT_ERROR_RATE_MIN_SAMPLES = 5
TEST_CONSOLE_MAX_TOKENS = 512

queue_state = QueueState()
start_time = time.time()


async def run_purge(app: FastAPI):
    runtime = app.state.runtime_settings
    await db.purge_all_tables(
        app.state.db,
        payload_retention_days=runtime.payload_retention_days,
        stats_retention_days=runtime.stats_retention_days,
        queue_retention_hours=runtime.retention_hours,
    )


async def periodic_purge(app: FastAPI):
    while True:
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)
        await run_purge(app)


async def periodic_state_sampler(app: FastAPI):
    # Queue depth + token level both drift between requests (token level
    # refills continuously; queue depth needs a timeline, not just push/pop
    # events) — sampled on an interval and persisted so the dashboard's
    # historical charts have real data to query, not just an in-memory
    # window that's empty whenever traffic is sparse.
    while True:
        for provider_id, provider in app.state.registry.providers.items():
            snapshot = await provider.capacity.snapshot()
            await db.insert_queue_sample(
                app.state.db,
                queue_depth=snapshot["queued"],
                token_level=snapshot["request_level"] or 0,
                provider_id=provider_id,
            )
        await asyncio.sleep(STATE_SAMPLE_INTERVAL_SECONDS)


async def reload_registry(app: FastAPI) -> None:
    previous_registry = getattr(app.state, "registry", None)
    stored = await db.list_providers(app.state.db)
    providers: list[tuple[ProviderConfig, dict[str, Any]]] = []
    for row in stored:
        encrypted = row.pop("encrypted_credentials", None)
        row.pop("models_updated_at", None)
        credentials = app.state.cipher.decrypt(encrypted) if encrypted else {}
        if not credentials.get("api_key") and row["id"] in app.state.legacy_api_keys:
            credentials["api_key"] = app.state.legacy_api_keys[row["id"]]
        providers.append((ProviderConfig.model_validate(row), credentials))
    stored_routes = await db.load_routes(app.state.db)
    routes = {alias: [RouteTarget.model_validate(target) for target in targets] for alias, targets in stored_routes.items()}
    registry = ProviderRegistry(providers, routes)
    registry.inherit_health(previous_registry)
    app.state.registry = registry
    default = app.state.registry.default
    app.state.bucket = default.capacity.request_bucket or TokenBucket(1_000_000, 1_000_000)
    app.state.model_routes = {alias: targets[0].model for alias, targets in routes.items() if targets}
    price_rows = await db.load_pricing(app.state.db)
    app.state.provider_pricing = {
        (item["provider_id"], item["model_id"]): {
            "input_per_1m": item["input_per_1m"], "output_per_1m": item["output_per_1m"]
        }
        for item in price_rows
    }
    app.state.pricing = {
        model: rates for (provider_id, model), rates in app.state.provider_pricing.items()
        if provider_id == default.config.id
    }


async def refresh_provider_health(app: FastAPI, provider_ids: set[str] | None = None) -> None:
    """Run non-billable discovery checks without delaying application startup."""
    async def check(runtime) -> None:
        started_at = time.time()
        try:
            models = await runtime.adapter.discover_models(app.state.client)
            health, error = ("healthy", "") if models else ("degraded", "Provider returned no recognizable models")
        except httpx.HTTPStatusError as exc:
            health = "auth_error" if exc.response.status_code in {401, 403} else "degraded"
            error = f"HTTP {exc.response.status_code}"
        except (httpx.RequestError, OutboundPolicyError, ValueError) as exc:
            health, error = "offline", str(exc)
        # Do not overwrite a newer inference/test result with an older check.
        if runtime.last_checked_at is None or runtime.last_checked_at <= started_at:
            runtime.mark_health(health, error)

    runtimes = (
        runtime
        for provider_id, runtime in app.state.registry.providers.items()
        if runtime.config.enabled and (provider_ids is None or provider_id in provider_ids)
    )
    await asyncio.gather(*(check(runtime) for runtime in runtimes))


def schedule_provider_health(app: FastAPI, provider_ids: set[str] | None = None) -> asyncio.Task:
    """Track health checks so provider changes stay non-blocking and shutdown-safe."""
    if provider_ids is not None:
        provider_ids = provider_ids | {
            provider_id
            for provider_id, runtime in app.state.registry.providers.items()
            if runtime.config.enabled and runtime.last_checked_at is None
        }
    task = asyncio.create_task(refresh_provider_health(app, provider_ids))
    tasks = getattr(app.state, "provider_health_tasks", None)
    if tasks is None:
        tasks = set()
        app.state.provider_health_tasks = tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


async def seed_provider_registry(app: FastAPI, runtime: RuntimeSettings) -> None:
    existing = await db.list_providers(app.state.db)
    if existing:
        return
    provider = provider_from_legacy(runtime.model_dump())
    encrypted: str | None = None
    if runtime.api_key:
        if app.state.cipher.available:
            encrypted = app.state.cipher.encrypt({"api_key": runtime.api_key})
        else:
            app.state.legacy_api_keys[provider.id] = runtime.api_key
            app.state.secret_migration_required = True
    await db.upsert_provider(app.state.db, provider.model_dump(), encrypted)
    if runtime.api_key and app.state.cipher.available:
        await db.delete_app_setting(app.state.db, "api_key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.security_enabled:
        required_security = {
            "SUPABASE_JWT_SECRET": settings.supabase_jwt_secret,
            "SUPABASE_JWT_ISSUER": settings.supabase_jwt_issuer,
            "API_KEY_PEPPER": settings.api_key_pepper,
        }
        missing = [name for name, value in required_security.items() if not value]
        if missing:
            raise RuntimeError(f"Security configuration is incomplete: {', '.join(missing)}")
    app.state.auth_client = SupabaseAuthClient(settings)
    app.state.security = SecurityService(settings, app.state.auth_client)
    app.state.outbound_policy = OutboundPolicy(
        disallowed_hosts=settings.outbound_disallowed_hosts,
        disallowed_cidrs=settings.outbound_disallowed_cidrs,
        disallowed_ports=settings.outbound_disallowed_ports,
    )
    app.state.client = ProtectedAsyncClient(app.state.outbound_policy)
    app.state.db = await db.connect(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )
    if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
        await app.state.auth_client.ensure_bootstrap_user(
            settings.bootstrap_admin_email.strip().lower(),
            settings.bootstrap_admin_password,
        )
    stored_settings = await db.get_app_settings(app.state.db)
    app.state.runtime_settings = RuntimeSettings.from_sources(settings, stored_settings)
    app.state.cipher = CredentialCipher(settings.settings_encryption_key)
    app.state.legacy_api_keys = {}
    app.state.secret_migration_required = False
    if not stored_settings:
        await db.save_app_settings(app.state.db, app.state.runtime_settings.model_dump())
    await seed_provider_registry(app, app.state.runtime_settings)
    await reload_registry(app)
    app.state.settings_lock = asyncio.Lock()
    await run_purge(app)
    app.state.purge_task = asyncio.create_task(periodic_purge(app))
    app.state.state_sampler_task = asyncio.create_task(periodic_state_sampler(app))
    app.state.provider_health_tasks = set()
    schedule_provider_health(app)
    yield
    tasks = [app.state.purge_task, app.state.state_sampler_task, *app.state.provider_health_tasks]
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    await app.state.client.aclose()
    await app.state.auth_client.close()
    await app.state.db.close()


app = FastAPI(title="PRISMUX", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
FRONTEND_DIST = Path("frontend/dist")
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")
if (FRONTEND_DIST / "brand").is_dir():
    app.mount("/brand", StaticFiles(directory=FRONTEND_DIST / "brand"), name="frontend-brand")


PUBLIC_APP_PATHS = {
    "/", "/live", "/history", "/test", "/settings", "/settings/providers",
    "/settings/routing", "/settings/pricing", "/settings/storage", "/settings/alerts",
    "/settings/access", "/dashboard",
}
PUBLIC_AUTH_PATHS = {"/api/auth/login", "/api/auth/refresh"}


def is_machine_api_path(path: str) -> bool:
    if path in {"/v1/chat/completions", "/v1/models"}:
        return True
    parts = [part for part in path.split("/") if part]
    return (
        len(parts) == 3 and parts[1:] == ["v1", "models"]
        or len(parts) == 4 and parts[1:] == ["v1", "chat", "completions"]
    )


def secure_response(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; "
        "img-src 'self' data:; font-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'"
    )
    if settings.cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def auth_error(message: str, status_code: int) -> JSONResponse:
    return secure_response(JSONResponse({"error": message}, status_code=status_code))


def csrf_valid(request: Request) -> bool:
    cookie = request.cookies.get(settings.csrf_cookie_name, "")
    header = request.headers.get("x-csrf-token", "")
    return bool(cookie and header and secrets.compare_digest(cookie, header))


@app.middleware("http")
async def security_boundary(request: Request, call_next):
    if not settings.security_enabled:
        return secure_response(await call_next(request))
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or path == "/health"
        or path in PUBLIC_APP_PATHS
        or path.startswith(("/assets/", "/brand/"))
    ):
        return secure_response(await call_next(request))
    if path in PUBLIC_AUTH_PATHS:
        if path == "/api/auth/refresh" and not csrf_valid(request):
            return auth_error("CSRF validation failed", 403)
        return secure_response(await call_next(request))

    security: SecurityService = request.app.state.security
    try:
        if is_machine_api_path(path):
            authorization = request.headers.get("authorization", "")
            if not authorization.startswith("Bearer "):
                raise AuthenticationError("A machine API key is required")
            principal = await security.machine_principal(request.app.state.db, authorization[7:].strip())
        else:
            token = request.cookies.get(settings.access_cookie_name, "")
            if not token:
                raise AuthenticationError("Authentication is required")
            principal = await security.user_principal(request.app.state.db, token)
            authorize_role(principal, required_role(request.method, path))
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not csrf_valid(request):
                raise AuthorizationError("CSRF validation failed")
        request.state.principal = principal
    except AuthenticationError:
        try:
            await db.insert_audit_event(
                request.app.state.db,
                actor_type="anonymous",
                actor_id=None,
                action="auth.machine" if is_machine_api_path(path) else "auth.session",
                outcome="denied",
                source_ip=security.client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        except Exception:
            pass
        return auth_error("Authentication failed", 401)
    except AuthorizationError as exc:
        return auth_error(str(exc), 403)

    response = await call_next(request)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and path not in {"/api/auth/logout"}:
        try:
            await db.insert_audit_event(
                request.app.state.db,
                actor_type=principal.actor_type,
                actor_id=principal.actor_id,
                action=f"{request.method} {path}",
                outcome="success" if response.status_code < 400 else "failure",
                source_ip=security.client_ip(request),
                user_agent=request.headers.get("user-agent"),
                details={"status_code": response.status_code},
            )
        except Exception:
            pass
    return secure_response(response)


def spa_response() -> FileResponse:
    if not FRONTEND_INDEX.is_file():
        raise HTTPException(status_code=503, detail="Dashboard assets are not built. Run npm run build in frontend/.")
    return FileResponse(FRONTEND_INDEX)


def extract_sse_error(text: str) -> dict | None:
    """Scans an SSE response body for a data: chunk carrying a top-level
    "error" envelope, e.g. `data: {"error":{"message":...,"type":...,"code":...}}`.
    NVIDIA can return HTTP 200 (stream opened fine) and then fail mid-stream —
    only the exact envelope shape counts as an error, never a substring match,
    so generated text that happens to mention "error" is never misclassified.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk_text = line[len("data:"):].strip()
        if not chunk_text or chunk_text == "[DONE]":
            continue
        try:
            chunk = json.loads(chunk_text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(chunk, dict):
            error = chunk.get("error")
            if isinstance(error, dict) and "message" in error:
                return error
    return None


def status_code_from_error(error: dict) -> int:
    code = error.get("code")
    if isinstance(code, bool):
        return 500
    if isinstance(code, int) and 100 <= code < 600:
        return code
    if isinstance(code, str) and code.isdigit() and 100 <= int(code) < 600:
        return int(code)
    return 500


def extract_preview(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return ""
    last_user = next(
        (m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"),
        messages[-1],
    )
    content = last_user.get("content") if isinstance(last_user, dict) else None
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict) and "text" in part
        )
    if not isinstance(content, str):
        content = "" if content is None else str(content)
    content = " ".join(content.split())
    if len(content) > PREVIEW_LENGTH:
        return content[:PREVIEW_LENGTH] + "…"
    return content


def extract_test_response_content(response_payload: Any) -> tuple[str | None, str | None, str | None]:
    """Keep final content distinct from provider-supplied reasoning output."""
    if not isinstance(response_payload, dict):
        return None, None, None
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, None, None
    choice = choices[0]
    message = choice.get("message")
    finish_reason = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None
    if not isinstance(message, dict):
        return None, None, finish_reason
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    return (
        content if isinstance(content, str) and content.strip() else None,
        reasoning if isinstance(reasoning, str) and reasoning.strip() else None,
        finish_reason,
    )


def format_row(row: dict) -> dict:
    request_payload = row["request_payload"]
    response_payload = row["response_payload"]
    upstream_model = request_payload.get("model") if isinstance(request_payload, dict) else row["model"]

    usage = None
    if row["prompt_tokens"] is not None or row["completion_tokens"] is not None:
        total = None
        if row["prompt_tokens"] is not None and row["completion_tokens"] is not None:
            total = row["prompt_tokens"] + row["completion_tokens"]
        usage = {
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": total,
        }

    return {
        "id": row["id"],
        "timestamp": db.parse_str(row["timestamp"]),
        "model": row["model"],
        "upstream_model": row.get("upstream_model") or upstream_model,
        "wait_ms": row["wait_ms"],
        "status": row["status"],
        "http_status": row["status_code"],
        "preview": extract_preview(request_payload),
        "request_payload": request_payload,
        "response_payload": response_payload,
        "usage": usage,
        "model_response_ms": row["model_response_ms"],
        "time_to_first_token_ms": row["time_to_first_token_ms"],
        "input_cost": row["input_cost"],
        "output_cost": row["output_cost"],
        "estimated_cost": row["estimated_cost"],
        "error_type": row["error_type"],
        "provider_id": row.get("provider_id"),
        "route_alias": row.get("route_alias"),
        "attempt_count": row.get("attempt_count", 1),
    }


def provider_cost_breakdown(
    app: FastAPI,
    provider_id: str,
    model: str,
    prompt: int | None,
    completion: int | None,
) -> dict[str, bool | float | None]:
    entry = app.state.provider_pricing.get((provider_id, model)) or app.state.provider_pricing.get((provider_id, "default"))
    if not entry:
        return {"priced": False, "input": None, "output": None, "total": None}
    input_cost = (prompt / 1_000_000 * entry["input_per_1m"]) if isinstance(prompt, int) else None
    output_cost = (completion / 1_000_000 * entry["output_per_1m"]) if isinstance(completion, int) else None
    total_cost = (input_cost + output_cost) if input_cost is not None and output_cost is not None else None
    return {"priced": True, "input": input_cost, "output": output_cost, "total": total_cost}


def provider_cost(app: FastAPI, provider_id: str, model: str, prompt: int | None, completion: int | None) -> float:
    return float(provider_cost_breakdown(app, provider_id, model, prompt, completion)["total"] or 0)


async def save_attempts(conn, request_id: int, attempts: list[dict]) -> None:
    for attempt in attempts:
        await db.insert_request_attempt(conn, request_id=request_id, **attempt)


async def forward_chat_request(app: FastAPI, payload: dict | None, body: bytes, *, is_test: bool = False) -> dict:
    if not isinstance(payload, dict):
        return {"status_code": 400, "logged_status_code": 400, "wait_ms": 0, "error": "Request body must be valid JSON", "response_payload": {"error": "Request body must be valid JSON"}, "usage": None, "model": "unknown", "upstream_model": "", "provider_id": None, "attempts": []}
    try:
        selector, route_targets = app.state.registry.resolve(payload.get("model"))
    except ValueError as exc:
        return {"status_code": 422, "logged_status_code": 422, "wait_ms": 0, "error": str(exc), "response_payload": {"error": str(exc)}, "usage": None, "model": payload.get("model") or "unknown", "upstream_model": "", "provider_id": None, "attempts": []}
    targets = app.state.registry.enabled_targets(route_targets)
    if not targets:
        return {"status_code": 503, "logged_status_code": 503, "wait_ms": 0, "error": "No enabled provider is available for this model route", "response_payload": {"error": "No enabled provider is available for this model route"}, "usage": None, "model": selector, "upstream_model": "", "provider_id": None, "attempts": []}

    original = sanitize(payload)
    stored = {**original, "_test": True} if is_test else original
    request_payload_json = json.dumps(stored)
    attempts: list[dict] = []
    last_error = "All provider targets failed"
    total_wait_ms = 0
    reservation = estimate_requested_tokens(payload)

    for index, (provider, upstream_model) in enumerate(targets, start=1):
        try:
            url, outgoing = provider.adapter.prepare_request(payload, upstream_model)
        except AdapterError as exc:
            return {"status_code": 422, "logged_status_code": 422, "wait_ms": total_wait_ms, "error": str(exc), "response_payload": {"error": str(exc)}, "usage": None, "model": selector, "upstream_model": upstream_model, "provider_id": provider.config.id, "attempts": attempts}
        await queue_state.inc(selector)
        try:
            wait_ms, reserved = await provider.capacity.acquire(reservation)
        finally:
            await queue_state.dec(selector)
        total_wait_ms += wait_ms
        started = time.monotonic()
        timeout = httpx.Timeout(provider.config.timeout_seconds, connect=min(10, provider.config.timeout_seconds))
        try:
            response = await app.state.client.post(url, json=outgoing, headers=provider.adapter.headers(), timeout=timeout)
        except OutboundPolicyError as exc:
            response_ms = round((time.monotonic() - started) * 1000)
            await provider.capacity.release(reserved, None)
            last_error = "Provider destination was blocked by outbound policy"
            provider.mark_health("offline", last_error)
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": response_ms, "status_code": None, "error_type": "blocked_destination", "fallback_reason": str(exc) if index < len(targets) else None})
            if index < len(targets):
                continue
            break
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            response_ms = round((time.monotonic() - started) * 1000)
            await provider.capacity.release(reserved, None)
            last_error = "Provider connection failed"
            provider.mark_health("offline", type(exc).__name__)
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": response_ms, "status_code": None, "error_type": "connection", "fallback_reason": str(type(exc).__name__) if index < len(targets) else None})
            if index < len(targets):
                continue
            break
        except httpx.TimeoutException:
            response_ms = round((time.monotonic() - started) * 1000)
            await provider.capacity.release(reserved, None)
            last_error = f"Provider did not respond within the configured {provider.config.timeout_seconds:g}s timeout"
            provider.mark_health("degraded", last_error)
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": response_ms, "status_code": None, "error_type": "timeout", "fallback_reason": None})
            break
        except httpx.RequestError as exc:
            response_ms = round((time.monotonic() - started) * 1000)
            await provider.capacity.release(reserved, None)
            last_error = "Provider request failed"
            provider.mark_health("offline", type(exc).__name__)
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": response_ms, "status_code": None, "error_type": "unknown", "fallback_reason": None})
            break

        response_ms = round((time.monotonic() - started) * 1000)
        provider.observe_status(response.status_code)
        try:
            response_body: Any = response.json()
        except ValueError:
            response_body = response.text
        if response.status_code in FALLBACK_STATUS_CODES and index < len(targets):
            await provider.capacity.release(reserved, None)
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": response_ms, "status_code": response.status_code, "error_type": db.classify_error_type(response.status_code), "fallback_reason": f"HTTP {response.status_code}"})
            continue

        if isinstance(response_body, dict) and response.status_code < 400:
            response_payload = provider.adapter.normalize_response(response_body, upstream_model)
        elif provider.config.adapter == "anthropic" and isinstance(response_body, dict):
            response_payload = {"error": response_body.get("error", response_body)}
        else:
            response_payload = response_body
        response_payload = sanitize(response_payload) if isinstance(response_payload, dict) else response_payload
        usage = response_payload.get("usage") if isinstance(response_payload, dict) else None
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        actual_tokens = (prompt_tokens + completion_tokens) if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int) else None
        cost = provider_cost_breakdown(app, provider.config.id, upstream_model, prompt_tokens, completion_tokens)
        await provider.capacity.release(reserved, actual_tokens)
        attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": response_ms, "status_code": response.status_code, "error_type": db.classify_error_type(response.status_code), "fallback_reason": None})
        route_alias = selector if selector in app.state.registry.routes else None
        request_id = await db.insert_request(
            app.state.db, model=selector, wait_ms=total_wait_ms, status_code=response.status_code,
            request_payload=request_payload_json, response_payload=json.dumps(response_payload),
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, model_response_ms=response_ms,
            input_cost=cost["input"], output_cost=cost["output"],
            estimated_cost=cost["total"],
            error_type=db.classify_error_type(response.status_code), provider_id=provider.config.id,
            upstream_model=upstream_model, route_alias=route_alias, attempt_count=len(attempts),
        )
        await save_attempts(app.state.db, request_id, attempts)
        return {"status_code": response.status_code, "logged_status_code": response.status_code, "wait_ms": total_wait_ms, "error": None, "raw_content": json.dumps(response_payload).encode() if isinstance(response_payload, dict) else response.content, "response_payload": response_payload, "usage": usage, "cost": cost, "model": selector, "upstream_model": upstream_model, "provider_id": provider.config.id, "attempts": attempts}

    final = attempts[-1] if attempts else {"provider_id": None, "upstream_model": "", "response_ms": 0}
    request_id = await db.insert_request(
        app.state.db, model=selector, wait_ms=total_wait_ms, status_code=None,
        request_payload=request_payload_json, response_payload=json.dumps({"error": last_error}),
        prompt_tokens=None, completion_tokens=None, model_response_ms=final.get("response_ms"), error_type=final.get("error_type", "unknown"),
        provider_id=final.get("provider_id"), upstream_model=final.get("upstream_model"),
        route_alias=selector if selector in app.state.registry.routes else None, attempt_count=len(attempts),
    )
    await save_attempts(app.state.db, request_id, attempts)
    return {"status_code": 502, "logged_status_code": None, "wait_ms": total_wait_ms, "error": last_error, "raw_content": None, "response_payload": {"error": last_error}, "usage": None, "model": selector, "upstream_model": final.get("upstream_model", ""), "provider_id": final.get("provider_id"), "attempts": attempts}


class StreamAccumulator:
    def __init__(self) -> None:
        self.response_id = ""
        self.content_parts: list[str] = []
        self.reasoning_parts: list[str] = []
        self.finish_reason: str | None = None
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.total_tokens: int | None = None
        self.error: dict[str, Any] | None = None
        self.tool_calls: dict[int, dict[str, Any]] = {}

    def consume(self, chunk: bytes) -> tuple[bool, bool]:
        done = False
        emitted_output = False
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            if data == "[DONE]":
                done = True
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            self.response_id = str(event.get("id") or self.response_id)
            if isinstance(event.get("error"), dict):
                self.error = event["error"]
            usage = event.get("usage")
            if isinstance(usage, dict):
                if isinstance(usage.get("prompt_tokens"), int):
                    self.prompt_tokens = usage["prompt_tokens"]
                if isinstance(usage.get("completion_tokens"), int):
                    self.completion_tokens = usage["completion_tokens"]
                if isinstance(usage.get("total_tokens"), int):
                    self.total_tokens = usage["total_tokens"]
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue
            choice = choices[0]
            if isinstance(choice.get("finish_reason"), str):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                self.content_parts.append(content)
                emitted_output = True
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                self.reasoning_parts.append(reasoning)
                emitted_output = True
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                call_index = int(call.get("index", 0))
                current = self.tool_calls.setdefault(call_index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if call.get("id"):
                    current["id"] = call["id"]
                function = call.get("function")
                if isinstance(function, dict):
                    current["function"]["name"] += str(function.get("name") or "")
                    current["function"]["arguments"] += str(function.get("arguments") or "")
                emitted_output = True
        return done, emitted_output

    def usage(self, request_payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        max_requested = int(request_payload.get("max_tokens") or request_payload.get("max_completion_tokens") or 0)
        prompt = self.prompt_tokens
        completion = self.completion_tokens
        estimated = False
        if prompt is None:
            prompt = max(1, estimate_requested_tokens(request_payload) - max_requested)
            estimated = True
        if completion is None:
            generated = "".join(self.content_parts) + "".join(self.reasoning_parts)
            completion = max(0, (len(generated) + 3) // 4)
            estimated = True
        total = self.total_tokens if self.total_tokens is not None else prompt + completion
        return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total, "estimated": estimated}, estimated

    def response_payload(self, model: str, usage: dict[str, Any]) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.content_parts) or None}
        if self.reasoning_parts:
            message["reasoning_content"] = "".join(self.reasoning_parts)
        if self.tool_calls:
            message["tool_calls"] = [self.tool_calls[index] for index in sorted(self.tool_calls)]
        payload: dict[str, Any] = {
            "id": self.response_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": self.finish_reason}],
            "usage": usage,
        }
        if self.error:
            payload["error"] = self.error
        return payload


async def streaming_proxy_response(app: FastAPI, payload: dict[str, Any], *, test_mode: bool = False) -> Response:
    try:
        selector, route_targets = app.state.registry.resolve(payload.get("model"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    targets = app.state.registry.enabled_targets(route_targets)
    if not targets:
        return JSONResponse({"error": "No enabled provider is available for this model route"}, status_code=503)
    reservation = estimate_requested_tokens(payload)
    attempts: list[dict] = []
    total_wait_ms = 0
    for index, (provider, upstream_model) in enumerate(targets, start=1):
        try:
            url, outgoing = provider.adapter.prepare_request(payload, upstream_model)
        except AdapterError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        await queue_state.inc(selector)
        try:
            wait_ms, reserved = await provider.capacity.acquire(reservation)
        finally:
            await queue_state.dec(selector)
        total_wait_ms += wait_ms
        started = time.monotonic()
        stream_context = app.state.client.stream(
            "POST", url, json=outgoing, headers=provider.adapter.headers(),
            timeout=httpx.Timeout(provider.config.timeout_seconds, connect=min(10, provider.config.timeout_seconds)),
        )
        try:
            response = await stream_context.__aenter__()
        except OutboundPolicyError as exc:
            await provider.capacity.release(reserved, None)
            provider.mark_health("offline", "Provider destination was blocked by outbound policy")
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": round((time.monotonic() - started) * 1000), "status_code": None, "error_type": "blocked_destination", "fallback_reason": str(exc) if index < len(targets) else None})
            if index < len(targets):
                continue
            return JSONResponse({"error": "Provider destination was blocked by outbound policy", "attempts": [{"provider_id": item["provider_id"], "error_type": item["error_type"]} for item in attempts]}, status_code=502)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            await provider.capacity.release(reserved, None)
            provider.mark_health("offline", type(exc).__name__)
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": round((time.monotonic() - started) * 1000), "status_code": None, "error_type": "connection", "fallback_reason": type(exc).__name__ if index < len(targets) else None})
            if index < len(targets):
                continue
            return JSONResponse({"error": "Provider connection failed", "attempts": [{"provider_id": item["provider_id"], "error_type": item["error_type"]} for item in attempts]}, status_code=502)
        except httpx.TimeoutException:
            await provider.capacity.release(reserved, None)
            message = f"Provider did not respond within the configured {provider.config.timeout_seconds:g}s timeout"
            provider.mark_health("degraded", message)
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": round((time.monotonic() - started) * 1000), "status_code": None, "error_type": "timeout", "fallback_reason": None})
            return JSONResponse({"error": message, "attempts": [{"provider_id": item["provider_id"], "error_type": item["error_type"]} for item in attempts]}, status_code=504)
        except httpx.RequestError as exc:
            await provider.capacity.release(reserved, None)
            provider.mark_health("offline", type(exc).__name__)
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": round((time.monotonic() - started) * 1000), "status_code": None, "error_type": "connection", "fallback_reason": None})
            return JSONResponse({"error": "Provider request failed", "attempts": [{"provider_id": item["provider_id"], "error_type": item["error_type"]} for item in attempts]}, status_code=502)
        provider.observe_status(response.status_code)
        if response.status_code in FALLBACK_STATUS_CODES and index < len(targets):
            await response.aread()
            await stream_context.__aexit__(None, None, None)
            await provider.capacity.release(reserved, None)
            attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": round((time.monotonic() - started) * 1000), "status_code": response.status_code, "error_type": db.classify_error_type(response.status_code), "fallback_reason": f"HTTP {response.status_code}"})
            continue

        async def generate():
            first_token_ms: int | None = None
            response_error: str | None = None
            accumulator = StreamAccumulator()
            final_result: dict[str, Any] | None = None
            try:
                if test_mode:
                    meta = {"provider_id": provider.config.id, "upstream_model": upstream_model, "model": selector, "wait_ms": total_wait_ms}
                    yield f"event: proxy.meta\ndata: {json.dumps(meta)}\n\n".encode()
                if provider.config.adapter == "anthropic":
                    iterator = provider.adapter.translate_stream(response.aiter_lines(), upstream_model)
                else:
                    async def openai_events():
                        async for line in response.aiter_lines():
                            if line.startswith("data:"):
                                yield f"{line}\n\n".encode()
                    iterator = openai_events()
                async for chunk in iterator:
                    done, emitted_output = accumulator.consume(chunk)
                    if first_token_ms is None and emitted_output:
                        first_token_ms = round((time.monotonic() - started) * 1000)
                    if not done:
                        yield chunk
            except asyncio.CancelledError:
                response_error = "cancelled"
                raise
            except (httpx.RequestError, json.JSONDecodeError) as exc:
                response_error = type(exc).__name__
                provider.mark_health("degraded", response_error)
                yield f"data: {json.dumps({'error': {'message': 'Provider stream interrupted', 'type': response_error}})}\n\n".encode()
            finally:
                response_ms = round((time.monotonic() - started) * 1000)
                await stream_context.__aexit__(None, None, None)
                usage, usage_estimated = accumulator.usage(payload)
                prompt_tokens = usage["prompt_tokens"]
                completion_tokens = usage["completion_tokens"]
                actual_tokens = prompt_tokens + completion_tokens
                await provider.capacity.release(reserved, actual_tokens)
                attempts.append({"attempt_number": index, "provider_id": provider.config.id, "upstream_model": upstream_model, "wait_ms": wait_ms, "response_ms": response_ms, "status_code": response.status_code if not response_error else None, "error_type": response_error, "fallback_reason": None})
                if not response_error:
                    provider.observe_status(response.status_code)
                cost = provider_cost_breakdown(app, provider.config.id, upstream_model, prompt_tokens, completion_tokens)
                response_payload = accumulator.response_payload(upstream_model, usage)
                stored_payload = sanitize(payload)
                if test_mode:
                    stored_payload = {**stored_payload, "_test": True}
                request_id = await db.insert_request(
                    app.state.db, model=selector, wait_ms=total_wait_ms,
                    status_code=response.status_code if not response_error else None,
                    request_payload=json.dumps(stored_payload), response_payload=json.dumps(sanitize(response_payload)),
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, model_response_ms=response_ms,
                    time_to_first_token_ms=first_token_ms, error_type=response_error or db.classify_error_type(response.status_code),
                    input_cost=cost["input"], output_cost=cost["output"], estimated_cost=cost["total"],
                    provider_id=provider.config.id, upstream_model=upstream_model,
                    route_alias=selector if selector in app.state.registry.routes else None, attempt_count=len(attempts),
                )
                await save_attempts(app.state.db, request_id, attempts)
                text, reasoning_text, finish_reason = extract_test_response_content(response_payload)
                final_result = {
                    "ok": response_error is None and response.status_code < 400 and accumulator.error is None,
                    "status_code": response.status_code if not response_error else None,
                    "wait_ms": total_wait_ms,
                    "error": response_error or (accumulator.error or {}).get("message"),
                    "text": text,
                    "reasoning_text": reasoning_text,
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "usage_estimated": usage_estimated,
                    "cost": cost,
                    "response_payload": response_payload,
                    "model": selector,
                    "upstream_model": upstream_model,
                    "provider_id": provider.config.id,
                    "attempts": attempts,
                }
            if test_mode and final_result is not None:
                yield f"event: proxy.result\ndata: {json.dumps(final_result)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        return StreamingResponse(generate(), status_code=response.status_code, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return JSONResponse({"error": "All provider targets failed"}, status_code=503)


@app.post("/v1/chat/completions")
async def proxy(request: Request):
    body = await request.body()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None

    if isinstance(payload, dict) and payload.get("stream") is True:
        return await streaming_proxy_response(request.app, payload)
    result = await forward_chat_request(request.app, payload, body)
    if result["error"] is not None:
        return JSONResponse({"error": result["error"], "attempts": result.get("attempts", [])}, status_code=result.get("status_code") or 502)

    return Response(content=result["raw_content"], status_code=result["status_code"], media_type="application/json")


def provider_scoped_payload(app: FastAPI, provider_id: str, payload: Any) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    runtime = app.state.registry.providers.get(provider_id)
    if runtime is None:
        return None, JSONResponse({"error": f"Provider '{provider_id}' was not found"}, status_code=404)
    if not runtime.config.enabled:
        return None, JSONResponse({"error": f"Provider '{provider_id}' is disabled"}, status_code=503)
    if not isinstance(payload, dict):
        return None, JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)

    requested_model = payload.get("model") or runtime.config.default_model
    if not isinstance(requested_model, str) or not requested_model.strip():
        return None, JSONResponse({"error": f"No model was supplied and provider '{provider_id}' has no default model"}, status_code=422)
    requested_model = requested_model.strip()
    if "::" in requested_model:
        requested_provider, requested_model = requested_model.split("::", 1)
        if requested_provider != provider_id or not requested_model:
            return None, JSONResponse({"error": f"This endpoint only accepts models owned by provider '{provider_id}'"}, status_code=422)

    return {**payload, "model": f"{provider_id}::{requested_model}"}, None


async def openai_models_payload(app: FastAPI, provider_id: str | None = None) -> dict[str, Any] | JSONResponse:
    if provider_id is not None:
        runtime = app.state.registry.providers.get(provider_id)
        if runtime is None:
            return JSONResponse({"error": f"Provider '{provider_id}' was not found"}, status_code=404)
        if not runtime.config.enabled:
            return JSONResponse({"error": f"Provider '{provider_id}' is disabled"}, status_code=503)
        models, _updated_at = await db.get_provider_models(app.state.db, provider_id)
        if not models and runtime.config.default_model:
            models = [runtime.config.default_model]
        return {"object": "list", "data": [{"id": model, "object": "model", "created": 0, "owned_by": provider_id} for model in models]}

    data: list[dict[str, Any]] = []
    for alias in sorted(app.state.registry.routes):
        data.append({"id": alias, "object": "model", "created": 0, "owned_by": "route"})
    for current_provider_id, runtime in app.state.registry.providers.items():
        if not runtime.config.enabled:
            continue
        models, _updated_at = await db.get_provider_models(app.state.db, current_provider_id)
        if not models and runtime.config.default_model:
            models = [runtime.config.default_model]
        data.extend({"id": f"{current_provider_id}::{model}", "object": "model", "created": 0, "owned_by": current_provider_id} for model in models)
    return {"object": "list", "data": data}


@app.get("/v1/models")
async def proxy_models(request: Request):
    return await openai_models_payload(request.app)


@app.get("/{provider_id}/v1/models")
async def provider_proxy_models(request: Request, provider_id: str):
    return await openai_models_payload(request.app, provider_id)


@app.post("/{provider_id}/v1/chat/completions")
async def provider_proxy(request: Request, provider_id: str):
    body = await request.body()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    scoped_payload, error = provider_scoped_payload(request.app, provider_id, payload)
    if error is not None:
        return error
    assert scoped_payload is not None
    if scoped_payload.get("stream") is True:
        return await streaming_proxy_response(request.app, scoped_payload)
    result = await forward_chat_request(request.app, scoped_payload, json.dumps(scoped_payload).encode())
    if result["error"] is not None:
        return JSONResponse({"error": result["error"], "attempts": result.get("attempts", [])}, status_code=result.get("status_code") or 502)
    return Response(content=result["raw_content"], status_code=result["status_code"], media_type="application/json")


class TestRequest(BaseModel):
    model: str
    content: str
    max_tokens: int | None = Field(default=None, gt=0, le=65_536)
    reasoning_mode: Literal["auto", "on", "off"] = "auto"


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=1024)


class AccessUserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = "viewer"
    password: str | None = Field(default=None, min_length=12, max_length=1024)


class AccessUserUpdate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str
    disabled: bool = False


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_at: datetime | None = None


def set_session_cookies(response: Response, session: dict[str, Any]) -> None:
    cookie_options = {
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "domain": settings.cookie_domain or None,
    }
    response.set_cookie(
        settings.access_cookie_name,
        session["access_token"],
        httponly=True,
        path="/",
        max_age=int(session.get("expires_in", 3600)),
        **cookie_options,
    )
    response.set_cookie(
        settings.refresh_cookie_name,
        session["refresh_token"],
        httponly=True,
        path="/api/auth",
        max_age=60 * 60 * 24 * 30,
        **cookie_options,
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        new_csrf_token(),
        httponly=False,
        path="/",
        max_age=60 * 60 * 24 * 30,
        **cookie_options,
    )


def clear_session_cookies(response: Response) -> None:
    for name, path in (
        (settings.access_cookie_name, "/"),
        (settings.refresh_cookie_name, "/api/auth"),
        (settings.csrf_cookie_name, "/"),
    ):
        response.delete_cookie(name, path=path, domain=settings.cookie_domain or None)


@app.post("/api/auth/login")
async def auth_login(request: Request, body: LoginRequest):
    security: SecurityService = request.app.state.security
    source_ip = security.client_ip(request) or "unknown"
    throttle_key = f"{source_ip}:{body.email.strip().lower()}"
    if not security.throttle.allowed(throttle_key):
        return JSONResponse({"error": "Authentication failed"}, status_code=429)
    try:
        session = await request.app.state.auth_client.password_login(body.email.strip().lower(), body.password)
        claims = security.validate_access_token(session["access_token"])
        role = await db.get_user_role(request.app.state.db, claims["sub"])
        if role is None:
            role = await db.bootstrap_first_admin(
                request.app.state.db,
                user_id=claims["sub"],
                email=claims.get("email") or body.email,
            )
        if role is None or role["disabled"]:
            raise AuthenticationError("Authentication failed")
        security.throttle.success(throttle_key)
        response = JSONResponse({"user": {"id": claims["sub"], "email": role["email"], "role": role["role"]}})
        set_session_cookies(response, session)
        await db.insert_audit_event(
            request.app.state.db, actor_type="user", actor_id=claims["sub"], action="auth.login",
            outcome="success", source_ip=security.client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        return response
    except (AuthenticationError, httpx.HTTPError):
        security.throttle.failure(throttle_key)
        await db.insert_audit_event(
            request.app.state.db, actor_type="anonymous", actor_id=None, action="auth.login",
            outcome="denied", source_ip=security.client_ip(request), user_agent=request.headers.get("user-agent"),
            details={"email_hash": hashlib.sha256(body.email.strip().lower().encode()).hexdigest()},
        )
        return JSONResponse({"error": "Authentication failed"}, status_code=401)


@app.post("/api/auth/refresh")
async def auth_refresh(request: Request):
    refresh_token = request.cookies.get(settings.refresh_cookie_name, "")
    if not refresh_token:
        return JSONResponse({"error": "Session could not be refreshed"}, status_code=401)
    try:
        session = await request.app.state.auth_client.refresh(refresh_token)
        principal = await request.app.state.security.user_principal(request.app.state.db, session["access_token"])
    except (AuthenticationError, httpx.HTTPError):
        response = JSONResponse({"error": "Session could not be refreshed"}, status_code=401)
        clear_session_cookies(response)
        return response
    response = JSONResponse({"user": {"id": principal.actor_id, "email": principal.email, "role": principal.role}})
    set_session_cookies(response, session)
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get(settings.access_cookie_name, "")
    try:
        await request.app.state.auth_client.logout(token)
    except AuthenticationError:
        pass
    principal = request.state.principal
    await db.insert_audit_event(
        request.app.state.db, actor_type="user", actor_id=principal.actor_id, action="auth.logout",
        outcome="success", source_ip=request.app.state.security.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    response = JSONResponse({"status": "logged_out"})
    clear_session_cookies(response)
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request):
    principal = request.state.principal
    return {"user": {"id": principal.actor_id, "email": principal.email, "role": principal.role}}


@app.get("/api/access/users")
async def access_users(request: Request):
    auth_users = await request.app.state.auth_client.list_users()
    roles = {str(item["user_id"]): item for item in await db.list_user_roles(request.app.state.db)}
    users = auth_users.get("users", auth_users if isinstance(auth_users, list) else [])
    return {
        "users": [
            {
                "id": item["id"], "email": item.get("email"), "created_at": item.get("created_at"),
                "last_sign_in_at": item.get("last_sign_in_at"),
                "role": roles.get(item["id"], {}).get("role"),
                "disabled": roles.get(item["id"], {}).get("disabled", True),
            }
            for item in users
        ]
    }


@app.post("/api/access/users")
async def access_user_create(request: Request, body: AccessUserCreate):
    if body.role not in {"admin", "operator", "viewer"}:
        return JSONResponse({"error": "Role must be admin, operator, or viewer"}, status_code=422)
    try:
        created = await request.app.state.auth_client.create_user(body.email.strip().lower(), body.password)
    except httpx.HTTPStatusError as exc:
        return JSONResponse({"error": "User could not be created", "status_code": exc.response.status_code}, status_code=400)
    user = created.get("user", created)
    await db.upsert_user_role(
        request.app.state.db, user_id=user["id"], email=user.get("email") or body.email, role=body.role
    )
    return {"user": {"id": user["id"], "email": user.get("email") or body.email, "role": body.role}}


@app.put("/api/access/users/{user_id}")
async def access_user_update(request: Request, user_id: str, body: AccessUserUpdate):
    if body.role not in {"admin", "operator", "viewer"}:
        return JSONResponse({"error": "Role must be admin, operator, or viewer"}, status_code=422)
    if user_id == request.state.principal.actor_id and (body.disabled or body.role != "admin"):
        return JSONResponse({"error": "Administrators cannot disable or demote their own account"}, status_code=409)
    await db.upsert_user_role(
        request.app.state.db, user_id=user_id, email=body.email, role=body.role, disabled=body.disabled
    )
    return {"user": {"id": user_id, **body.model_dump()}}


@app.get("/api/access/keys")
async def access_keys(request: Request):
    return {"keys": await db.list_proxy_api_keys(request.app.state.db)}


@app.post("/api/access/keys")
async def access_key_create(request: Request, body: ApiKeyCreate):
    expires_at = body.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at <= datetime.now(timezone.utc):
        return JSONResponse({"error": "Expiration must be in the future"}, status_code=422)
    raw_key, prefix, digest = request.app.state.security.create_machine_key()
    key_id = new_key_id()
    await db.insert_proxy_api_key(
        request.app.state.db, key_id=key_id, name=body.name.strip(), prefix=prefix, digest=digest,
        created_by=request.state.principal.actor_id, scopes=["proxy:invoke"],
        expires_at=expires_at.isoformat() if expires_at else None,
    )
    return {"key": {"id": key_id, "name": body.name.strip(), "prefix": prefix, "scopes": ["proxy:invoke"], "expires_at": expires_at, "secret": raw_key}}


@app.delete("/api/access/keys/{key_id}")
async def access_key_revoke(request: Request, key_id: str):
    if not await db.revoke_proxy_api_key(request.app.state.db, key_id):
        return JSONResponse({"error": "API key not found or already revoked"}, status_code=404)
    return {"status": "revoked", "id": key_id}


@app.get("/api/access/audit")
async def access_audit(request: Request, limit: int = Query(default=100, ge=1, le=500)):
    return {"events": await db.list_audit_events(request.app.state.db, limit=limit)}


class PricingModelUpdate(BaseModel):
    input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)


class SettingsUpdate(BaseModel):
    base_url: str
    models_url: str = ""
    api_key: str | None = None
    clear_api_key: bool = False
    default_model: str = ""
    rate_limit_rpm: float = Field(gt=0, le=1_000_000)
    bucket_capacity: float = Field(gt=0, le=1_000_000)
    retention_hours: float = Field(gt=0)
    payload_retention_days: float = Field(gt=0)
    stats_retention_days: float = Field(gt=0)
    pricing_models: dict[str, PricingModelUpdate] | None = None
    model_routes: dict[str, str] | None = None
    alert_queue_seconds: float = Field(gt=0)
    alert_error_rate_pct: float = Field(ge=0, le=100)
    alert_rpm_pct: float = Field(ge=0, le=100)


class ModelDiscoveryRequest(BaseModel):
    base_url: str
    models_url: str = ""
    api_key: str | None = None


class ProviderUpdate(ProviderConfig):
    api_key: str | None = None
    clear_api_key: bool = False
    secret_headers: dict[str, str] | None = None
    clear_secret_headers: bool = False


class ProviderModelDiscoveryRequest(BaseModel):
    base_url: str | None = None
    models_url: str | None = None
    adapter: str | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None
    anthropic_version: str | None = None


class OutboundPolicyPreviewRequest(BaseModel):
    base_url: str
    models_url: str = ""


class RoutesUpdate(BaseModel):
    routes: dict[str, list[RouteTarget]]


class PricingEntryUpdate(BaseModel):
    provider_id: str
    model_id: str = Field(min_length=1)
    input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)


class PricingUpdate(BaseModel):
    prices: list[PricingEntryUpdate]


def build_test_payload(app: FastAPI, body: TestRequest, *, stream: bool = False) -> tuple[str, dict[str, Any]]:
    model = body.model.strip() or "unknown"
    max_tokens = body.max_tokens
    if max_tokens is None:
        try:
            _selector, route_targets = app.state.registry.resolve(model)
            targets = app.state.registry.enabled_targets(route_targets)
            if targets:
                provider, upstream_model = targets[0]
                capability = describe_model_capabilities(provider.config, upstream_model)
                max_tokens = int(capability["default_max_tokens"])
        except ValueError:
            pass
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": body.content}],
        "max_tokens": max_tokens or TEST_CONSOLE_MAX_TOKENS,
        "_reasoning_mode": body.reasoning_mode,
    }
    if stream:
        include_usage = False
        try:
            _selector, route_targets = app.state.registry.resolve(model)
            targets = app.state.registry.enabled_targets(route_targets)
            if targets:
                provider, upstream_model = targets[0]
                include_usage = bool(describe_model_capabilities(provider.config, upstream_model)["stream_usage"])
        except ValueError:
            pass
        payload.update({"stream": True, "_include_usage": include_usage})
    return model, payload


@app.post("/test")
async def test_request(request: Request, body: TestRequest):
    model, payload = build_test_payload(request.app, body)
    result = await forward_chat_request(request.app, payload, json.dumps(payload).encode(), is_test=True)

    response_payload = result["response_payload"]
    text, reasoning_text, finish_reason = extract_test_response_content(response_payload)

    # Use logged_status_code (not the raw wire-level status) so the Test
    # Console shows the true outcome even for a stream that opened with
    # HTTP 200 but carried an error chunk mid-stream.
    reported_status_code = result["logged_status_code"]
    return {
        "ok": result["error"] is None and reported_status_code is not None and reported_status_code < 400,
        "status_code": reported_status_code,
        "wait_ms": result["wait_ms"],
        "error": result["error"],
        "text": text,
        "reasoning_text": reasoning_text,
        "finish_reason": finish_reason,
        "usage": result["usage"],
        "cost": result.get("cost"),
        "response_payload": response_payload,
        "model": model,
        "upstream_model": result["upstream_model"],
        "provider_id": result["provider_id"],
        "attempts": result["attempts"],
    }


@app.post("/test/stream")
async def stream_test_request(request: Request, body: TestRequest):
    _model, payload = build_test_payload(request.app, body, stream=True)
    return await streaming_proxy_response(request.app, payload, test_mode=True)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - start_time),
    }


def settings_response(app: FastAPI) -> dict:
    runtime = app.state.runtime_settings
    default = app.state.registry.default
    public_settings = runtime.model_dump(exclude={"api_key"})
    public_settings.update({
        "base_url": default.config.base_url,
        "models_url": default.config.models_url,
        "default_model": default.config.default_model,
        "rate_limit_rpm": default.config.rate_limit_rpm or 1_000_000,
        "bucket_capacity": default.config.request_burst,
    })
    return {
        "settings": public_settings,
        "configuration": {
            "model_routes": app.state.model_routes,
            "pricing_models": app.state.pricing,
        },
        "provider": detect_provider(default.config.base_url),
        "resolved_models_url": resolve_models_url(default.config.base_url, default.config.models_url),
        "api_key_configured": default.api_key_configured,
        "encryption": {"available": app.state.cipher.available, "migration_required": app.state.secret_migration_required, "error": app.state.cipher.error},
        "deployment": {
            "proxy_port": settings.proxy_port,
            "database": "PostgreSQL",
        },
    }


@app.get("/api/settings")
async def get_settings(request: Request):
    return settings_response(request.app)


@app.put("/api/settings")
async def update_settings(request: Request, body: SettingsUpdate):
    app = request.app
    async with app.state.settings_lock:
        default = app.state.registry.default
        api_key = default.adapter.api_key
        if body.clear_api_key:
            api_key = ""
        elif body.api_key and body.api_key.strip():
            api_key = body.api_key.strip()

        values = body.model_dump(exclude={"api_key", "clear_api_key", "pricing_models", "model_routes"})
        pricing_rows = None
        if body.pricing_models is not None:
            pricing_values = {model.strip(): rates.model_dump() for model, rates in body.pricing_models.items()}
            if any(not model for model in pricing_values):
                return JSONResponse({"error": "Pricing model IDs cannot be empty"}, status_code=422)
            if len(pricing_values) != len(body.pricing_models):
                return JSONResponse({"error": "Pricing model IDs must be unique"}, status_code=422)
            pricing_rows = [
                {"provider_id": default.config.id, "model_id": model, **rates} for model, rates in pricing_values.items()
            ]

        route_rows = None
        if body.model_routes is not None:
            route_values = {alias.strip(): model.strip() for alias, model in body.model_routes.items()}
            if any(not alias or not model for alias, model in route_values.items()):
                return JSONResponse({"error": "Model route aliases and model IDs cannot be empty"}, status_code=422)
            if len(route_values) != len(body.model_routes):
                return JSONResponse({"error": "Model route aliases must be unique"}, status_code=422)
            route_rows = {
                alias: [{"provider_id": default.config.id, "model": model}] for alias, model in route_values.items()
            }
        try:
            updated = RuntimeSettings.model_validate({**values, "api_key": api_key})
        except ValidationError as exc:
            return JSONResponse(
                {"error": "Invalid settings", "details": exc.errors(include_url=False)},
                status_code=422,
            )
        stored_values = updated.model_dump(exclude={"api_key"})
        provider_config = default.config.model_copy(update={
            "base_url": updated.base_url, "models_url": updated.models_url,
            "default_model": updated.default_model, "rate_limit_rpm": updated.rate_limit_rpm,
            "request_burst": updated.bucket_capacity,
        })
        encrypted = None
        if body.clear_api_key:
            encrypted = ""
        elif body.api_key and body.api_key.strip():
            if not app.state.cipher.available:
                return JSONResponse({"error": app.state.cipher.error or "SETTINGS_ENCRYPTION_KEY is required before secrets can be saved"}, status_code=422)
            encrypted = app.state.cipher.encrypt({"api_key": api_key, "secret_headers": default.adapter.secret_headers})

        # Perform persistent writes only after all validation and encryption
        # preconditions pass, so a rejected legacy settings request cannot
        # partially replace structured routes or pricing.
        if pricing_rows is not None:
            await db.replace_pricing(app.state.db, pricing_rows)
        if route_rows is not None:
            await db.replace_routes(app.state.db, route_rows)
        await db.save_app_settings(app.state.db, stored_values)
        await db.upsert_provider(app.state.db, provider_config.model_dump(), encrypted)
        app.state.runtime_settings = updated
        await reload_registry(app)
        schedule_provider_health(app, {provider_config.id})
        await run_purge(app)

    return settings_response(app)


@app.post("/api/settings/models")
async def get_provider_models(request: Request, body: ModelDiscoveryRequest):
    current = request.app.state.runtime_settings
    resolved_models_url = resolve_models_url(body.base_url, body.models_url)
    supplied_api_key = body.api_key.strip() if body.api_key and body.api_key.strip() else ""
    api_key = supplied_api_key or (
        current.api_key if same_origin(resolved_models_url, current.base_url) else ""
    )
    try:
        return await discover_models(
            request.app.state.client,
            base_url=body.base_url,
            api_key=api_key,
            models_url=body.models_url,
        )
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            {"error": f"Model discovery returned HTTP {exc.response.status_code}"},
            status_code=400,
        )
    except (httpx.RequestError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def provider_payload(app: FastAPI, provider_id: str) -> dict:
    runtime = app.state.registry.providers[provider_id]
    models, updated_at = await db.get_provider_models(app.state.db, provider_id)
    payload = provider_public(runtime, models, updated_at)
    payload["network_policy"] = await outbound_policy_payload(
        app, runtime.config.base_url, runtime.config.models_url
    )
    return payload


async def outbound_policy_payload(app: FastAPI, base_url: str, models_url: str = "") -> dict[str, Any]:
    destinations = {"base_url": base_url}
    if models_url:
        if not same_origin(base_url, models_url):
            return {
                "status": "denied",
                "error": "Models URL must use the same origin as the provider base URL",
                "destinations": [],
            }
        destinations["models_url"] = models_url
    inspected: list[dict[str, Any]] = []
    try:
        for name, url in destinations.items():
            decision = await app.state.outbound_policy.inspect(url)
            inspected.append({
                "field": name,
                "normalized_url": decision.normalized_url,
                "hostname": decision.hostname,
                "port": decision.port,
                "addresses": list(decision.addresses),
                "classification": decision.classification,
            })
    except OutboundPolicyError as exc:
        return {"status": "denied", "error": str(exc), "destinations": inspected}
    return {"status": "allowed", "error": None, "destinations": inspected}


async def require_allowed_provider_destination(app: FastAPI, config: ProviderConfig) -> None:
    preview = await outbound_policy_payload(app, config.base_url, config.models_url)
    if preview["status"] != "allowed":
        raise OutboundPolicyError(preview["error"] or "Provider destination is blocked")


def validate_secret_headers(headers: dict[str, str] | None) -> dict[str, str]:
    cleaned = {}
    for name, value in (headers or {}).items():
        normalized = name.strip()
        if not normalized or normalized.lower() in RESERVED_HEADERS:
            raise ValueError(f"Header '{name}' is reserved or invalid")
        cleaned[normalized] = str(value)
    return cleaned


@app.get("/api/providers")
async def list_provider_api(request: Request):
    providers = [await provider_payload(request.app, provider_id) for provider_id in request.app.state.registry.providers]
    return {
        "providers": providers,
        "presets": PROVIDER_PRESETS,
        "encryption": {
            "available": request.app.state.cipher.available,
            "migration_required": request.app.state.secret_migration_required,
            "error": request.app.state.cipher.error,
        },
    }


@app.post("/api/providers/policy-preview")
async def preview_provider_policy(request: Request, body: OutboundPolicyPreviewRequest):
    result = await outbound_policy_payload(request.app, body.base_url, body.models_url)
    return JSONResponse(result, status_code=200 if result["status"] == "allowed" else 422)


@app.post("/api/providers")
async def create_provider_api(request: Request, body: ProviderUpdate):
    app = request.app
    if await db.get_provider(app.state.db, body.id):
        return JSONResponse({"error": "A provider with this ID already exists"}, status_code=409)
    config = ProviderConfig.model_validate(body.model_dump(exclude={"api_key", "clear_api_key", "secret_headers", "clear_secret_headers"}))
    try:
        await require_allowed_provider_destination(app, config)
    except OutboundPolicyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    if not app.state.registry.providers:
        config.is_default = True
    try:
        secret_headers = validate_secret_headers(body.secret_headers)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    encrypted = None
    if (body.api_key and body.api_key.strip()) or secret_headers:
        if not app.state.cipher.available:
            return JSONResponse({"error": app.state.cipher.error or "SETTINGS_ENCRYPTION_KEY is required before secrets can be saved"}, status_code=422)
        encrypted = app.state.cipher.encrypt({"api_key": (body.api_key or "").strip(), "secret_headers": secret_headers})
    await db.upsert_provider(app.state.db, config.model_dump(), encrypted)
    await reload_registry(app)
    schedule_provider_health(app, {config.id})
    return await provider_payload(app, config.id)


@app.get("/api/providers/{provider_id}")
async def get_provider_api(request: Request, provider_id: str):
    if provider_id not in request.app.state.registry.providers:
        return JSONResponse({"error": "Provider not found"}, status_code=404)
    return await provider_payload(request.app, provider_id)


@app.put("/api/providers/{provider_id}")
async def update_provider_api(request: Request, provider_id: str, body: ProviderUpdate):
    app = request.app
    current = app.state.registry.providers.get(provider_id)
    if not current:
        return JSONResponse({"error": "Provider not found"}, status_code=404)
    if body.id != provider_id:
        return JSONResponse({"error": "Provider IDs are immutable"}, status_code=422)
    if current.config.is_default and not body.is_default:
        return JSONResponse({"error": "Choose another default provider before removing the current default"}, status_code=409)
    config = ProviderConfig.model_validate(body.model_dump(exclude={"api_key", "clear_api_key", "secret_headers", "clear_secret_headers"}))
    try:
        await require_allowed_provider_destination(app, config)
    except OutboundPolicyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    existing_secret_headers = current.adapter.secret_headers
    api_key = current.adapter.api_key
    credentials_changed = False
    if body.clear_api_key:
        api_key = ""; credentials_changed = True
    elif body.api_key and body.api_key.strip():
        api_key = body.api_key.strip(); credentials_changed = True
    if body.clear_secret_headers:
        secret_headers = {}; credentials_changed = True
    elif body.secret_headers is not None:
        try:
            secret_headers = validate_secret_headers(body.secret_headers)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        credentials_changed = True
    else:
        secret_headers = existing_secret_headers
    encrypted = None
    if credentials_changed:
        if (api_key or secret_headers) and not app.state.cipher.available:
            return JSONResponse({"error": app.state.cipher.error or "SETTINGS_ENCRYPTION_KEY is required before secrets can be saved"}, status_code=422)
        encrypted = app.state.cipher.encrypt({"api_key": api_key, "secret_headers": secret_headers}) if (api_key or secret_headers) else ""
    await db.upsert_provider(app.state.db, config.model_dump(), encrypted)
    await reload_registry(app)
    schedule_provider_health(app, {provider_id})
    return await provider_payload(app, provider_id)


@app.delete("/api/providers/{provider_id}")
async def delete_provider_api(request: Request, provider_id: str):
    runtime = request.app.state.registry.providers.get(provider_id)
    if not runtime:
        return JSONResponse({"error": "Provider not found"}, status_code=404)
    if runtime.config.is_default:
        return JSONResponse({"error": "The default provider cannot be deleted"}, status_code=409)
    references = await db.provider_route_references(request.app.state.db, provider_id)
    if references:
        return JSONResponse({"error": "Provider is referenced by model routes", "routes": references}, status_code=409)
    await db.delete_provider(request.app.state.db, provider_id)
    await reload_registry(request.app)
    return {"status": "deleted", "id": provider_id}


@app.post("/api/providers/{provider_id}/models")
async def discover_provider_models(
    request: Request,
    provider_id: str,
    body: ProviderModelDiscoveryRequest | None = None,
):
    runtime = request.app.state.registry.providers.get(provider_id)
    if not runtime:
        return JSONResponse({"error": "Provider not found"}, status_code=404)
    try:
        adapter = runtime.adapter
        models_url = resolve_models_url(runtime.config.base_url, runtime.config.models_url)
        if body:
            overrides = {
                key: value for key, value in body.model_dump(exclude_none=True, exclude={"api_key"}).items()
            }
            config = ProviderConfig.model_validate({**runtime.config.model_dump(), **overrides})
            supplied_key = (body.api_key or "").strip()
            adapter = make_adapter(config, supplied_key or runtime.adapter.api_key, runtime.adapter.secret_headers)
            models_url = resolve_models_url(config.base_url, config.models_url)
        models = await adapter.discover_models(request.app.state.client)
        if not models:
            raise ValueError("The provider returned no recognizable model IDs")
        updated_at = await db.replace_provider_models(request.app.state.db, provider_id, models)
        runtime.mark_health("healthy")
        return {"provider_id": provider_id, "models": models, "models_url": models_url, "models_updated_at": updated_at, "health": runtime.health}
    except httpx.HTTPStatusError as exc:
        runtime.mark_health("auth_error" if exc.response.status_code in {401, 403} else "degraded", f"HTTP {exc.response.status_code}")
        return JSONResponse({"error": f"Model discovery returned HTTP {exc.response.status_code}"}, status_code=400)
    except (httpx.RequestError, ValueError) as exc:
        runtime.mark_health("offline", str(exc))
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/providers/{provider_id}/health")
async def provider_health_api(request: Request, provider_id: str):
    result = await discover_provider_models(request, provider_id, None)
    if isinstance(result, JSONResponse):
        return result
    return {"provider_id": provider_id, "health": "healthy", "checked_at": time.time(), "models_count": len(result["models"])}


@app.post("/api/providers/{provider_id}/test")
async def provider_test_api(request: Request, provider_id: str, body: TestRequest):
    if provider_id not in request.app.state.registry.providers:
        return JSONResponse({"error": "Provider not found"}, status_code=404)
    model = body.model.strip() or request.app.state.registry.providers[provider_id].config.default_model
    qualified = TestRequest(model=f"{provider_id}::{model}", content=body.content, max_tokens=body.max_tokens, reasoning_mode=body.reasoning_mode)
    _selector, payload = build_test_payload(request.app, qualified)
    result = await forward_chat_request(request.app, payload, json.dumps(payload).encode(), is_test=True)
    return result


@app.get("/api/settings/routes")
async def get_routes_api(request: Request):
    return {"routes": await db.load_routes(request.app.state.db)}


@app.put("/api/settings/routes")
async def update_routes_api(request: Request, body: RoutesUpdate):
    provider_ids = set(request.app.state.registry.providers)
    normalized: dict[str, list[dict[str, str]]] = {}
    for raw_alias, targets in body.routes.items():
        alias = raw_alias.strip()
        if not alias or "::" in alias:
            return JSONResponse({"error": "Route aliases cannot be empty or contain ::"}, status_code=422)
        if alias in normalized:
            return JSONResponse({"error": "Route aliases must remain unique after trimming"}, status_code=422)
        if not targets:
            return JSONResponse({"error": f"Route '{alias}' needs at least one target"}, status_code=422)
        normalized[alias] = []
        for target in targets:
            if target.provider_id not in provider_ids:
                return JSONResponse({"error": f"Unknown provider '{target.provider_id}'"}, status_code=422)
            model = target.model.strip()
            if not model:
                return JSONResponse({"error": f"Route '{alias}' contains an empty model ID"}, status_code=422)
            normalized[alias].append({"provider_id": target.provider_id, "model": model})
    await db.replace_routes(request.app.state.db, normalized)
    await reload_registry(request.app)
    return {"routes": await db.load_routes(request.app.state.db)}


@app.get("/api/settings/pricing")
async def get_pricing_api(request: Request):
    return {"prices": await db.load_pricing(request.app.state.db)}


@app.put("/api/settings/pricing")
async def update_pricing_api(request: Request, body: PricingUpdate):
    provider_ids = set(request.app.state.registry.providers)
    rows = [item.model_dump() for item in body.prices]
    keys = [(item["provider_id"], item["model_id"].strip()) for item in rows]
    if any(not model_id for _, model_id in keys):
        return JSONResponse({"error": "Pricing model IDs cannot be empty"}, status_code=422)
    if len(keys) != len(set(keys)):
        return JSONResponse({"error": "Provider and model pricing pairs must be unique"}, status_code=422)
    if any(provider_id not in provider_ids for provider_id, _ in keys):
        return JSONResponse({"error": "Pricing contains an unknown provider"}, status_code=422)
    for row in rows:
        row["model_id"] = row["model_id"].strip()
    await db.replace_pricing(request.app.state.db, rows)
    await reload_registry(request.app)
    return {"prices": await db.load_pricing(request.app.state.db)}


async def evaluate_alerts(app: FastAPI) -> list[dict]:
    conn = app.state.db
    runtime = app.state.runtime_settings
    alerts = []

    if await db.has_sustained_queueing(conn, runtime.alert_queue_seconds):
        alerts.append({
            "id": "sustained_throttling",
            "severity": "warning",
            "message": f"Requests have been queued continuously for over {int(runtime.alert_queue_seconds)}s. The proxy is throttling sustained traffic against the {runtime.rate_limit_rpm:g} rpm limit.",
        })

    error_count = await db.count_status_since(conn, "error", ALERT_ERROR_RATE_WINDOW_SECONDS)
    total_count = await db.count_status_since(conn, "success", ALERT_ERROR_RATE_WINDOW_SECONDS) \
        + await db.count_status_since(conn, "throttled", ALERT_ERROR_RATE_WINDOW_SECONDS) + error_count
    if total_count >= ALERT_ERROR_RATE_MIN_SAMPLES:
        error_rate_pct = (error_count / total_count) * 100
        if error_rate_pct > runtime.alert_error_rate_pct:
            alerts.append({
                "id": "elevated_error_rate",
                "severity": "critical",
                "message": f"Error rate is {error_rate_pct:.0f}% over the last 5 minutes ({error_count}/{total_count} requests), above the {runtime.alert_error_rate_pct:g}% threshold.",
            })

    rpm_30s = await db.count_status_since(conn, "success", 30) \
        + await db.count_status_since(conn, "throttled", 30) + await db.count_status_since(conn, "error", 30)
    rpm_30s = rpm_30s * 2  # scale 30s count to a per-minute rate
    if runtime.rate_limit_rpm > 0:
        rpm_pct = (rpm_30s / runtime.rate_limit_rpm) * 100
        if rpm_pct > runtime.alert_rpm_pct:
            alerts.append({
                "id": "approaching_rate_limit",
                "severity": "warning",
                "message": f"Sustained request rate is at {rpm_pct:.0f}% of the {runtime.rate_limit_rpm:g} rpm ceiling, approaching the configured limit.",
            })

    if await db.upstream_all_failed_recent(conn, 3):
        alerts.append({
            "id": "upstream_down",
            "severity": "critical",
            "message": "The last 3 upstream requests failed. The provider may be down or misconfigured.",
        })

    return alerts


@app.get("/stats")
async def get_stats(request: Request):
    conn = request.app.state.db
    runtime = request.app.state.runtime_settings
    level = await request.app.state.bucket.level()
    default_provider = request.app.state.registry.default
    provider_stats = []
    for provider_id, provider_runtime in request.app.state.registry.providers.items():
        capacity = await provider_runtime.capacity.snapshot()
        provider_stats.append({
            "id": provider_id,
            "name": provider_runtime.config.name,
            "preset": provider_runtime.config.preset,
            "enabled": provider_runtime.config.enabled,
            "is_default": provider_runtime.config.is_default,
            "health": provider_runtime.health,
            "health_error": provider_runtime.last_health_error,
            "last_checked_at": provider_runtime.last_checked_at,
            "rate_limit_rpm": provider_runtime.config.rate_limit_rpm,
            "tokens_per_minute": provider_runtime.config.tokens_per_minute,
            "max_concurrency": provider_runtime.config.max_concurrency,
            **capacity,
        })

    global_stats = await db.global_stats(conn, RECENT_WINDOW_SECONDS)
    per_model = await db.per_model_stats(conn, RECENT_WINDOW_SECONDS)
    last_outcome = await db.latest_status(conn)
    queue_snapshot = await queue_state.snapshot()

    queued_last_hour = await db.count_queued_since(conn, TREND_WINDOW_SECONDS)
    throttled_last_hour = await db.count_status_since(conn, "throttled", TREND_WINDOW_SECONDS)
    avg_wait_recent = await db.avg_wait_last_n(conn, ROLLING_AVG_WAIT_N)
    avg_response_recent = await db.avg_response_last_n(conn, ROLLING_AVG_WAIT_N)
    avg_ttft_recent = await db.avg_time_to_first_token_last_n(conn, ROLLING_AVG_WAIT_N)
    avg_wait_recent_n = min(ROLLING_AVG_WAIT_N, global_stats["total_requests"])

    cost_and_tokens_today = await db.today_cost_and_tokens(conn)
    cost_per_hour = await db.cost_per_hour_buckets(conn, COST_SPARKLINE_HOURS)

    alerts = await evaluate_alerts(request.app)

    model_defaults = {
        "total_requests": 0, "requests_per_minute": 0.0, "avg_wait_ms": 0,
        "total_tokens": 0, "total_cost": 0.0, "avg_tokens_per_request": 0, "avg_cost_per_request": 0.0,
    }
    by_model = {}
    for name in set(per_model) | set(queue_snapshot["by_model"]):
        model_stats = per_model.get(name, model_defaults)
        by_model[name] = {**model_stats, "queued": queue_snapshot["by_model"].get(name, 0)}

    recent_rows = await db.recent_requests(conn, limit=PAGE_SIZE)

    return {
        "total_requests": global_stats["total_requests"],
        "queued": queue_snapshot["queued"],
        "queued_last_hour": queued_last_hour,
        "token_level": round(level, 3),
        "bucket_capacity": default_provider.config.request_burst,
        "requests_per_minute": global_stats["requests_per_minute"],
        "rate_limit_rpm": default_provider.config.rate_limit_rpm or 1_000_000,
        "avg_wait_ms": global_stats["avg_wait_ms"],
        "avg_wait_recent_ms": avg_wait_recent,
        "avg_wait_recent_n": avg_wait_recent_n,
        "avg_response_recent_ms": avg_response_recent,
        "avg_time_to_first_token_ms": avg_ttft_recent,
        "throttled_last_hour": throttled_last_hour,
        "last_outcome": last_outcome,
        "payload_retention_days": runtime.payload_retention_days,
        "provider": detect_provider(default_provider.config.base_url),
        "providers": provider_stats,
        "cost_today": cost_and_tokens_today["cost_today"],
        "prompt_tokens_today": cost_and_tokens_today["prompt_tokens_today"],
        "completion_tokens_today": cost_and_tokens_today["completion_tokens_today"],
        "cost_per_hour": cost_per_hour,
        "alerts": alerts,
        "by_model": by_model,
        "recent": [format_row(row) for row in recent_rows],
        "start_time": start_time,
        "uptime_seconds": round(time.time() - start_time),
    }


@app.get("/metrics")
async def get_metrics(
    request: Request,
    range: str = Query(default=DEFAULT_RANGE),
    provider_id: str | None = Query(default=None),
):
    """Cost/token/latency/error breakdowns for the Overview page's second
    row of charts — mirrors /charts' range handling (same presets, same
    bucket-count math) so both sets of charts stay in lockstep when the
    shared time-range selector changes.
    """
    conn = request.app.state.db
    range_key = range if (range in RANGE_PRESETS or range == "all") else DEFAULT_RANGE

    oldest_age_seconds = None
    if range_key == "all":
        oldest_ts = await db.oldest_request_timestamp(conn, provider_id)
        if oldest_ts is not None:
            oldest_age_seconds = time.time() - db.parse_str(oldest_ts)

    window_seconds, bucket_seconds, bucket_count = resolve_range(range_key, oldest_age_seconds)

    token_usage = await db.token_usage_buckets(conn, bucket_seconds, window_seconds, bucket_count, provider_id)
    latency = await db.latency_buckets(conn, bucket_seconds, window_seconds, bucket_count, provider_id)
    percentiles = await db.latency_percentiles(conn, window_seconds, provider_id)
    histogram = await db.tokens_histogram(conn, window_seconds, provider_id)
    errors = await db.error_breakdown(conn, window_seconds, provider_id)

    return {
        "range": range_key,
        "bucket_seconds": bucket_seconds,
        "bucket_count": bucket_count,
        "window_seconds": window_seconds,
        "provider_id": provider_id,
        "token_usage": token_usage,
        "latency": latency,
        "percentiles": percentiles,
        "tokens_histogram": histogram,
        "error_breakdown": errors,
        "generated_at": time.time(),
    }


@app.get("/charts")
async def get_charts(
    request: Request,
    range: str = Query(default=DEFAULT_RANGE),
    provider_id: str | None = Query(default=None),
):
    conn = request.app.state.db
    runtime = request.app.state.runtime_settings
    range_key = range if (range in RANGE_PRESETS or range == "all") else DEFAULT_RANGE

    oldest_age_seconds = None
    if range_key == "all":
        oldest_ts = await db.oldest_request_timestamp(conn, provider_id)
        if oldest_ts is not None:
            oldest_age_seconds = time.time() - db.parse_str(oldest_ts)

    window_seconds, bucket_seconds, bucket_count = resolve_range(range_key, oldest_age_seconds)

    status = await db.chart_status_breakdown(conn, bucket_seconds, window_seconds, bucket_count, provider_id)
    queue = await db.queue_sample_buckets(conn, bucket_seconds, window_seconds, bucket_count, provider_id)
    is_empty = sum(status["success"]) + sum(status["throttled"]) + sum(status["error"]) == 0
    selected = request.app.state.registry.providers.get(provider_id) if provider_id else request.app.state.registry.default
    rpm_limit = selected.config.rate_limit_rpm if selected else runtime.rate_limit_rpm
    ceiling_per_bucket = (rpm_limit * bucket_seconds) / 60 if rpm_limit and rpm_limit > 0 else 0

    return {
        "range": range_key,
        "bucket_seconds": bucket_seconds,
        "bucket_count": bucket_count,
        "window_seconds": window_seconds,
        "provider_id": provider_id,
        "is_empty": is_empty,
        "ceiling_per_bucket": round(ceiling_per_bucket, 2),
        "status": status,
        "queue_depth": queue["queue_depth"],
        "token_level": queue["token_level"],
        "generated_at": time.time(),
    }


@app.get("/requests")
async def list_requests(
    request: Request,
    before_id: int | None = Query(default=None),
    limit: int = Query(default=PAGE_SIZE, le=200, gt=0),
    provider_id: str | None = Query(default=None),
):
    conn = request.app.state.db
    rows = await db.recent_requests(conn, limit=limit, before_id=before_id, provider_id=provider_id)
    return {
        "requests": [format_row(row) for row in rows],
        "has_more": len(rows) == limit,
    }


def build_log_filters(
    start_ts: float | None, end_ts: float | None, model: str | None, status: str | None, search: str | None,
    provider_id: str | None = None,
) -> dict:
    return {
        "start_ts": db.epoch_to_str(start_ts) if start_ts is not None else None,
        "end_ts": db.epoch_to_str(end_ts) if end_ts is not None else None,
        "model": model or None,
        "status": status if status in ("success", "throttled", "error") else None,
        "search": search.strip() if search else None,
        "provider_id": provider_id or None,
    }


def format_search_row(row: dict) -> dict:
    request_payload = row["request_payload"]
    upstream_model = request_payload.get("model") if isinstance(request_payload, dict) else row["model"]

    usage = None
    if row["prompt_tokens"] is not None or row["completion_tokens"] is not None:
        total = None
        if row["prompt_tokens"] is not None and row["completion_tokens"] is not None:
            total = row["prompt_tokens"] + row["completion_tokens"]
        usage = {
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": total,
        }

    return {
        "id": row["id"],
        "timestamp": db.parse_str(row["timestamp"]),
        "model": row["model"],
        "upstream_model": row.get("upstream_model") or upstream_model,
        "wait_ms": row["wait_ms"],
        "status": row["status"],
        "http_status": row["status_code"],
        "preview": extract_preview(request_payload),
        "request_payload": request_payload,
        "response_payload": row["response_payload"],
        "payload_available": row["payload_available"],
        "usage": usage,
        "model_response_ms": row["model_response_ms"],
        "time_to_first_token_ms": row["time_to_first_token_ms"],
        "input_cost": row["input_cost"],
        "output_cost": row["output_cost"],
        "estimated_cost": row["estimated_cost"],
        "error_type": row["error_type"],
        "provider_id": row.get("provider_id"),
        "route_alias": row.get("route_alias"),
        "attempt_count": row.get("attempt_count", 1),
    }


@app.get("/api/requests")
async def api_search_requests(
    request: Request,
    start_ts: float | None = Query(default=None),
    end_ts: float | None = Query(default=None),
    model: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    before_id: int | None = Query(default=None),
    limit: int = Query(default=PAGE_SIZE, le=200, gt=0),
    provider_id: str | None = Query(default=None),
):
    conn = request.app.state.db
    filters = build_log_filters(start_ts, end_ts, model, status, search, provider_id)
    rows = await db.search_requests(conn, limit=limit, before_id=before_id, **filters)
    total_matching = await db.count_matching(conn, **filters)
    return {
        "requests": [format_search_row(row) for row in rows],
        "has_more": len(rows) == limit,
        "total_matching": total_matching,
    }


@app.delete("/api/requests/{request_id}")
async def api_delete_request(request: Request, request_id: int):
    deleted = await db.delete_request(request.app.state.db, request_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": request_id}


@app.delete("/api/requests")
async def api_delete_matching(
    request: Request,
    start_ts: float | None = Query(default=None),
    end_ts: float | None = Query(default=None),
    model: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    confirm: bool = Query(default=False),
    provider_id: str | None = Query(default=None),
):
    """Bulk delete. Called once with confirm=false to preview the match count
    for the confirmation dialog, then again with confirm=true to execute —
    keeps the "how many rows will this delete" number honest without a
    separate endpoint.
    """
    conn = request.app.state.db
    filters = build_log_filters(start_ts, end_ts, model, status, search, provider_id)
    ids = await db.find_matching_ids(conn, **filters)
    if not confirm:
        return {"matched": len(ids), "deleted": False}
    await db.delete_by_ids(conn, ids)
    return {"matched": len(ids), "deleted": True}


@app.get("/api/requests/export")
async def api_export_requests(
    request: Request,
    start_ts: float | None = Query(default=None),
    end_ts: float | None = Query(default=None),
    model: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    format: str = Query(default="json"),
    provider_id: str | None = Query(default=None),
):
    conn = request.app.state.db
    filters = build_log_filters(start_ts, end_ts, model, status, search, provider_id)
    rows = await db.search_requests(conn, limit=db.EXPORT_MAX_ROWS, before_id=None, **filters)
    formatted = [format_search_row(row) for row in rows]

    if format == "csv":
        buf = io.StringIO()
        fieldnames = [
            "id", "timestamp", "provider_id", "route_alias", "model", "upstream_model", "attempt_count", "status", "http_status", "wait_ms",
            "model_response_ms", "time_to_first_token_ms", "input_cost", "output_cost", "estimated_cost", "error_type",
            "prompt_tokens", "completion_tokens", "preview", "request_payload", "response_payload",
        ]
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in formatted:
            csv_row = dict(row)
            csv_row["request_payload"] = json.dumps(row["request_payload"]) if row["request_payload"] else ""
            csv_row["response_payload"] = json.dumps(row["response_payload"]) if row["response_payload"] else ""
            writer.writerow(csv_row)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=requests_export.csv"},
        )

    return Response(
        content=json.dumps(formatted, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=requests_export.json"},
    )


@app.post("/reset")
async def reset(request: Request):
    await db.reset_all(request.app.state.db)
    await queue_state.reset()
    return {"status": "reset"}


@app.get("/")
async def page_overview():
    return spa_response()


@app.get("/live")
async def page_live():
    return spa_response()


@app.get("/history")
async def page_history():
    return spa_response()


@app.get("/test")
async def page_test():
    return spa_response()


@app.get("/guide")
async def page_guide():
    return spa_response()


@app.get("/settings")
@app.get("/settings/providers")
@app.get("/settings/routing")
@app.get("/settings/pricing")
@app.get("/settings/storage")
@app.get("/settings/alerts")
@app.get("/settings/access")
async def page_settings():
    return spa_response()


@app.get("/dashboard")
async def dashboard_redirect():
    # Old single-page dashboard URL — kept working as a redirect after the
    # multi-page split so existing bookmarks/docs don't break.
    return RedirectResponse(url="/")
