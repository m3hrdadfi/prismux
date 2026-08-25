import json
from datetime import datetime, timezone

from app.database import Database, Row

DatabaseConnection = Database
DatabaseRow = Row

REQUESTS_MAX_ROWS = 10_000
REQUEST_STATS_MAX_ROWS = 2_000_000  # backstop; stats_retention_days is the real cap
QUEUE_SAMPLES_MAX_ROWS = 200_000  # ~55h at 1/sec; retention_hours is the real cap
EXPORT_MAX_ROWS = 50_000
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

# Shared status classification, reused by both the Request Log filters and
# the Overview page's rolling trend counts. Priority matches the row-level
# color logic used across the dashboard: error > throttled > success.
STATUS_CONDITIONS = {
    "error": "(rs.status_code IS NULL OR (rs.status_code >= 400 AND rs.status_code != 429))",
    "throttled": "(rs.status_code = 429 OR (rs.status_code < 400 AND rs.wait_ms > 2000))",
    "success": "(rs.status_code IS NOT NULL AND rs.status_code < 400 AND rs.status_code != 429 AND rs.wait_ms <= 2000)",
}


def now_str() -> datetime:
    return datetime.now(timezone.utc)


def epoch_to_str(epoch_seconds: float) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)


def parse_str(value: str) -> float:
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc).timestamp()


def status_label(status_code: int | None) -> str:
    if status_code is None:
        return "error"
    if status_code == 429:
        return "429"
    if status_code >= 400:
        return "error"
    return "success"


def classify_error_type(status_code: int | None) -> str | None:
    """Derives error_type from an HTTP status code. Returns None for success.
    Network-level failures (status_code is None) aren't classified here since
    the caller already knows whether it was a timeout or something else —
    pass that through explicitly instead of guessing from a null status.
    """
    if status_code is None:
        return None
    if status_code == 429:
        return "rate_limited"
    if 500 <= status_code < 600:
        return "server_error"
    if 400 <= status_code < 500:
        return "client_error"
    return None


async def connect(database_url: str, *, pool_size: int = 10, max_overflow: int = 10) -> DatabaseConnection:
    """Connect to the migrated PostgreSQL schema.

    Schema creation is intentionally absent. Alembic is the only component
    allowed to perform DDL in production.
    """
    return await Database.connect(database_url, pool_size=pool_size, max_overflow=max_overflow)


async def get_app_settings(conn: DatabaseConnection) -> dict:
    cursor = await conn.execute("SELECT key, value FROM app_settings")
    rows = await cursor.fetchall()
    await cursor.close()
    values = {}
    for row in rows:
        if not isinstance(row["value"], str):
            values[row["key"]] = row["value"]
            continue
        try:
            values[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            values[row["key"]] = row["value"]
    return values


async def save_app_settings(conn: DatabaseConnection, values: dict) -> None:
    timestamp = now_str()
    await conn.executemany(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        [(key, json.dumps(value), timestamp) for key, value in values.items()],
    )
    await conn.commit()


async def delete_app_setting(conn: DatabaseConnection, key: str) -> None:
    await conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    await conn.commit()


async def list_providers(conn: DatabaseConnection) -> list[dict]:
    cursor = await conn.execute(
        """SELECT p.*, pc.encrypted_value,
                  (SELECT MAX(discovered_at) FROM provider_models pm WHERE pm.provider_id = p.id) AS models_updated_at
           FROM providers p LEFT JOIN provider_credentials pc ON pc.provider_id = p.id
           ORDER BY p.is_default DESC, p.name COLLATE NOCASE"""
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [
        {
            **json.loads(row["config_json"]),
            "encrypted_credentials": row["encrypted_value"],
            "models_updated_at": row["models_updated_at"],
        }
        for row in rows
    ]


async def get_provider(conn: DatabaseConnection, provider_id: str) -> dict | None:
    providers = await list_providers(conn)
    return next((provider for provider in providers if provider["id"] == provider_id), None)


async def upsert_provider(conn: DatabaseConnection, config: dict, encrypted_credentials: str | None = None) -> None:
    timestamp = now_str()
    if config.get("is_default"):
        await conn.execute("UPDATE providers SET is_default = 0")
        cursor = await conn.execute("SELECT id, config_json FROM providers")
        for row in await cursor.fetchall():
            stored = json.loads(row["config_json"])
            stored["is_default"] = False
            await conn.execute("UPDATE providers SET config_json = ? WHERE id = ?", (json.dumps(stored), row["id"]))
        await cursor.close()
    await conn.execute(
        """INSERT INTO providers (id, name, config_json, enabled, is_default, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, config_json=excluded.config_json,
             enabled=excluded.enabled, is_default=excluded.is_default, updated_at=excluded.updated_at""",
        (config["id"], config["name"], json.dumps(config), bool(config.get("enabled", True)), bool(config.get("is_default", False)), timestamp, timestamp),
    )
    if encrypted_credentials is not None:
        if encrypted_credentials:
            await conn.execute(
                """INSERT INTO provider_credentials (provider_id, encrypted_value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(provider_id) DO UPDATE SET encrypted_value=excluded.encrypted_value, updated_at=excluded.updated_at""",
                (config["id"], encrypted_credentials, timestamp),
            )
        else:
            await conn.execute("DELETE FROM provider_credentials WHERE provider_id = ?", (config["id"],))
    await conn.commit()


async def delete_provider(conn: DatabaseConnection, provider_id: str) -> None:
    await conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
    await conn.commit()


async def provider_route_references(conn: DatabaseConnection, provider_id: str) -> list[str]:
    cursor = await conn.execute("SELECT DISTINCT alias FROM route_targets WHERE provider_id = ? ORDER BY alias", (provider_id,))
    aliases = [row["alias"] for row in await cursor.fetchall()]
    await cursor.close()
    return aliases


async def replace_provider_models(conn: DatabaseConnection, provider_id: str, models: list[str]) -> str:
    timestamp = now_str()
    await conn.execute("DELETE FROM provider_models WHERE provider_id = ?", (provider_id,))
    await conn.executemany(
        "INSERT INTO provider_models (provider_id, model_id, discovered_at) VALUES (?, ?, ?)",
        [(provider_id, model, timestamp) for model in models],
    )
    await conn.commit()
    return timestamp


async def get_provider_models(conn: DatabaseConnection, provider_id: str) -> tuple[list[str], str | None]:
    cursor = await conn.execute(
        "SELECT model_id, discovered_at FROM provider_models WHERE provider_id = ? ORDER BY model_id COLLATE NOCASE",
        (provider_id,),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [row["model_id"] for row in rows], (rows[0]["discovered_at"] if rows else None)


async def load_routes(conn: DatabaseConnection) -> dict[str, list[dict[str, str]]]:
    cursor = await conn.execute(
        """SELECT r.alias, t.provider_id, t.model_id FROM model_routes r
           JOIN route_targets t ON t.alias = r.alias WHERE r.enabled = 1 ORDER BY r.alias, t.priority"""
    )
    result: dict[str, list[dict[str, str]]] = {}
    for row in await cursor.fetchall():
        result.setdefault(row["alias"], []).append({"provider_id": row["provider_id"], "model": row["model_id"]})
    await cursor.close()
    return result


async def replace_routes(conn: DatabaseConnection, routes: dict[str, list[dict[str, str]]]) -> None:
    timestamp = now_str()
    await conn.execute("DELETE FROM route_targets")
    await conn.execute("DELETE FROM model_routes")
    for alias, targets in routes.items():
        await conn.execute("INSERT INTO model_routes (alias, enabled, updated_at) VALUES (?, 1, ?)", (alias, timestamp))
        await conn.executemany(
            "INSERT INTO route_targets (alias, priority, provider_id, model_id) VALUES (?, ?, ?, ?)",
            [(alias, index, target["provider_id"], target["model"]) for index, target in enumerate(targets)],
        )
    await conn.commit()


async def load_pricing(conn: DatabaseConnection) -> list[dict]:
    cursor = await conn.execute(
        "SELECT provider_id, model_id, input_per_1m, output_per_1m FROM model_pricing ORDER BY provider_id, model_id"
    )
    rows = [dict(row) for row in await cursor.fetchall()]
    await cursor.close()
    return rows


async def replace_pricing(conn: DatabaseConnection, prices: list[dict]) -> None:
    timestamp = now_str()
    await conn.execute("DELETE FROM model_pricing")
    await conn.executemany(
        "INSERT INTO model_pricing (provider_id, model_id, input_per_1m, output_per_1m, updated_at) VALUES (?, ?, ?, ?, ?)",
        [(item["provider_id"], item["model_id"], item["input_per_1m"], item["output_per_1m"], timestamp) for item in prices],
    )
    await conn.commit()


async def insert_request(
    conn: DatabaseConnection,
    *,
    model: str,
    wait_ms: int,
    status_code: int | None,
    request_payload: str | None,
    response_payload: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    model_response_ms: int | None = None,
    time_to_first_token_ms: int | None = None,
    input_cost: float | None = None,
    output_cost: float | None = None,
    estimated_cost: float | None = None,
    error_type: str | None = None,
    provider_id: str | None = None,
    upstream_model: str | None = None,
    route_alias: str | None = None,
    attempt_count: int = 1,
) -> int:
    ts = now_str()
    cursor = await conn.execute(
        """
        INSERT INTO requests
            (timestamp, model, wait_ms, status_code, request_payload, response_payload, prompt_tokens, completion_tokens,
             model_response_ms, time_to_first_token_ms, input_cost, output_cost, estimated_cost, error_type,
             provider_id, upstream_model, route_alias, attempt_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts, model, wait_ms, status_code, request_payload, response_payload, prompt_tokens, completion_tokens,
            model_response_ms, time_to_first_token_ms, input_cost, output_cost, estimated_cost, error_type,
            provider_id, upstream_model, route_alias, attempt_count,
        ),
    )
    row_id = cursor.lastrowid
    await conn.execute(
        """
        INSERT INTO request_stats
            (id, timestamp, model, status_code, wait_ms, prompt_tokens, completion_tokens,
             model_response_ms, time_to_first_token_ms, input_cost, output_cost, estimated_cost, error_type,
             provider_id, upstream_model, route_alias, attempt_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id, ts, model, status_code, wait_ms, prompt_tokens, completion_tokens,
            model_response_ms, time_to_first_token_ms, input_cost, output_cost, estimated_cost, error_type,
            provider_id, upstream_model, route_alias, attempt_count,
        ),
    )
    await conn.commit()
    return row_id


async def insert_request_attempt(
    conn: DatabaseConnection, *, request_id: int, attempt_number: int, provider_id: str,
    upstream_model: str, wait_ms: int, response_ms: int | None, status_code: int | None,
    error_type: str | None, fallback_reason: str | None,
) -> None:
    await conn.execute(
        """INSERT INTO request_attempts
           (request_id, attempt_number, provider_id, upstream_model, wait_ms, response_ms, status_code, error_type, fallback_reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (request_id, attempt_number, provider_id, upstream_model, wait_ms, response_ms, status_code, error_type, fallback_reason, now_str()),
    )
    await conn.commit()


async def insert_queue_sample(conn: DatabaseConnection, *, queue_depth: int, token_level: float, provider_id: str | None = None) -> None:
    await conn.execute(
        "INSERT INTO queue_samples (timestamp, queue_depth, token_level, provider_id) VALUES (?, ?, ?, ?)",
        (now_str(), queue_depth, round(token_level, 4), provider_id),
    )
    await conn.commit()


async def _purge_table(conn: DatabaseConnection, table: str, cutoff_modifier: str, max_rows: int) -> None:
    # `table` is always one of our own fixed table names, never external input.
    await conn.execute(
        f"DELETE FROM {table} WHERE id IN ("
        f"SELECT id FROM {table} WHERE timestamp < (CURRENT_TIMESTAMP - CAST(CAST(? AS text) AS interval)) "
        f"ORDER BY id LIMIT 5000)",
        (cutoff_modifier,),
    )
    await conn.execute(
        f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} ORDER BY id DESC OFFSET ? LIMIT 5000)",
        (max_rows,),
    )


async def purge_all_tables(
    conn: DatabaseConnection,
    *,
    payload_retention_days: float,
    stats_retention_days: float,
    queue_retention_hours: float,
) -> None:
    await conn.begin()
    try:
        cursor = await conn.execute("SELECT pg_try_advisory_xact_lock(8217062402) AS acquired")
        lock = await cursor.fetchone()
        if not lock or not lock["acquired"]:
            await conn.rollback()
            return
        await _purge_table(conn, "requests", f"{payload_retention_days} days", REQUESTS_MAX_ROWS)
        await _purge_table(conn, "request_stats", f"{stats_retention_days} days", REQUEST_STATS_MAX_ROWS)
        await _purge_table(conn, "queue_samples", f"{queue_retention_hours} hours", QUEUE_SAMPLES_MAX_ROWS)
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise


async def reset_all(conn: DatabaseConnection) -> None:
    await conn.execute("DELETE FROM requests")
    await conn.execute("DELETE FROM request_stats")
    await conn.execute("DELETE FROM queue_samples")
    await conn.commit()


def _row_to_dict(row: DatabaseRow) -> dict:
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "model": row["model"],
        "wait_ms": row["wait_ms"],
        "status_code": row["status_code"],
        "status": status_label(row["status_code"]),
        "request_payload": json.loads(row["request_payload"]) if row["request_payload"] else None,
        "response_payload": json.loads(row["response_payload"]) if row["response_payload"] else None,
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "model_response_ms": row["model_response_ms"],
        "time_to_first_token_ms": row["time_to_first_token_ms"],
        "input_cost": row["input_cost"],
        "output_cost": row["output_cost"],
        "estimated_cost": row["estimated_cost"],
        "error_type": row["error_type"],
        "provider_id": row["provider_id"],
        "upstream_model": row["upstream_model"],
        "route_alias": row["route_alias"],
        "attempt_count": row["attempt_count"],
    }


async def recent_requests(conn: DatabaseConnection, limit: int = 50, before_id: int | None = None, provider_id: str | None = None) -> list[dict]:
    """Live-tail source for the Recent Requests panel — always within the
    payload retention window in practice, so it reads `requests` directly."""
    if before_id is not None and provider_id:
        cursor = await conn.execute(
            "SELECT * FROM requests WHERE id < ? AND provider_id = ? ORDER BY id DESC LIMIT ?",
            (before_id, provider_id, limit),
        )
    elif before_id is not None:
        cursor = await conn.execute(
            "SELECT * FROM requests WHERE id < ? ORDER BY id DESC LIMIT ?",
            (before_id, limit),
        )
    elif provider_id:
        cursor = await conn.execute("SELECT * FROM requests WHERE provider_id = ? ORDER BY id DESC LIMIT ?", (provider_id, limit))
    else:
        cursor = await conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,))
    rows = await cursor.fetchall()
    await cursor.close()
    return [_row_to_dict(r) for r in rows]


async def latest_status(conn: DatabaseConnection) -> str:
    cursor = await conn.execute("SELECT status_code FROM request_stats ORDER BY id DESC LIMIT 1")
    row = await cursor.fetchone()
    await cursor.close()
    return "unknown" if row is None else status_label(row["status_code"])


async def oldest_request_timestamp(conn: DatabaseConnection, provider_id: str | None = None) -> str | None:
    sql = "SELECT MIN(timestamp) AS ts FROM request_stats"
    params: tuple = ()
    if provider_id:
        sql += " WHERE provider_id = ?"
        params = (provider_id,)
    cursor = await conn.execute(sql, params)
    row = await cursor.fetchone()
    await cursor.close()
    return row["ts"] if row else None


async def global_stats(conn: DatabaseConnection, recent_window_seconds: int = 60) -> dict:
    # request_stats is the long-lived source of truth for totals/rpm — it
    # outlives the payload-bearing `requests` table by design.
    window = f"-{recent_window_seconds} seconds"

    cursor = await conn.execute("SELECT COUNT(*) AS c FROM request_stats")
    total_requests = (await cursor.fetchone())["c"]
    await cursor.close()

    cursor = await conn.execute(
        "SELECT COUNT(*) AS c, AVG(wait_ms) AS avg_wait FROM request_stats WHERE timestamp > datetime('now', ?)",
        (window,),
    )
    row = await cursor.fetchone()
    await cursor.close()

    recent_count = row["c"] or 0
    avg_wait_ms = round(row["avg_wait"]) if row["avg_wait"] is not None else 0
    rpm = recent_count * (60 / recent_window_seconds)

    return {
        "total_requests": total_requests,
        "requests_per_minute": round(rpm, 1),
        "avg_wait_ms": avg_wait_ms,
    }


async def per_model_stats(conn: DatabaseConnection, recent_window_seconds: int = 60) -> dict:
    window = f"-{recent_window_seconds} seconds"
    cursor = await conn.execute(
        """
        SELECT
            model,
            COUNT(*) AS total,
            SUM(CASE WHEN timestamp > datetime('now', ?) THEN 1 ELSE 0 END) AS recent_count,
            AVG(CASE WHEN timestamp > datetime('now', ?) THEN wait_ms END) AS recent_avg_wait,
            SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS total_tokens,
            SUM(COALESCE(estimated_cost, 0)) AS total_cost
        FROM request_stats
        GROUP BY model
        """,
        (window, window),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    result = {}
    for row in rows:
        recent_count = row["recent_count"] or 0
        rpm = recent_count * (60 / recent_window_seconds)
        avg_wait_ms = round(row["recent_avg_wait"]) if row["recent_avg_wait"] is not None else 0
        total = row["total"]
        total_tokens = row["total_tokens"] or 0
        total_cost = row["total_cost"] or 0.0
        result[row["model"]] = {
            "total_requests": total,
            "requests_per_minute": round(rpm, 1),
            "avg_wait_ms": avg_wait_ms,
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
            "avg_tokens_per_request": round(total_tokens / total, 1) if total else 0,
            "avg_cost_per_request": round(total_cost / total, 6) if total else 0.0,
        }
    return result


async def count_queued_since(conn: DatabaseConnection, window_seconds: int) -> int:
    """How many requests had to wait at all (wait_ms > 0) in the trailing window —
    gives the Queued stat card context beyond "0 right now"."""
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM request_stats WHERE wait_ms > 0 AND timestamp > datetime('now', ?)",
        (f"-{window_seconds} seconds",),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row["c"] or 0


async def count_status_since(conn: DatabaseConnection, status: str, window_seconds: int) -> int:
    condition = STATUS_CONDITIONS.get(status)
    if condition is None:
        return 0
    cursor = await conn.execute(
        f"SELECT COUNT(*) AS c FROM request_stats rs WHERE {condition} AND timestamp > datetime('now', ?)",
        (f"-{window_seconds} seconds",),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row["c"] or 0


async def avg_wait_last_n(conn: DatabaseConnection, n: int) -> int:
    """Rolling average wait over the most recent N requests — more honest
    than an all-time average that dilutes toward zero as history grows."""
    cursor = await conn.execute(
        "SELECT AVG(wait_ms) AS avg_wait FROM (SELECT wait_ms FROM request_stats ORDER BY id DESC LIMIT ?)",
        (n,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return round(row["avg_wait"]) if row["avg_wait"] is not None else 0


async def avg_response_last_n(conn: DatabaseConnection, n: int) -> int:
    """Rolling average model response time (NVIDIA-side latency, excluding
    queue wait) over the most recent N requests — sibling metric to
    avg_wait_last_n so the two "what's slow" signals stay directly comparable."""
    cursor = await conn.execute(
        "SELECT AVG(model_response_ms) AS avg_ms FROM (SELECT model_response_ms FROM request_stats ORDER BY id DESC LIMIT ?)",
        (n,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return round(row["avg_ms"]) if row["avg_ms"] is not None else 0


async def avg_time_to_first_token_last_n(conn: DatabaseConnection, n: int) -> int | None:
    """None when no row in the sample has a value at all (e.g. this proxy
    isn't forwarding streaming requests) — distinct from 0ms, which would be
    a real (implausible) measurement."""
    cursor = await conn.execute(
        """
        SELECT AVG(time_to_first_token_ms) AS avg_ms, COUNT(time_to_first_token_ms) AS have
        FROM (SELECT time_to_first_token_ms FROM request_stats ORDER BY id DESC LIMIT ?)
        """,
        (n,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if not row["have"]:
        return None
    return round(row["avg_ms"])


async def today_cost_and_tokens(conn: DatabaseConnection) -> dict:
    """"Today" = since local-midnight-in-UTC — simplest consistent definition
    given the DB stores everything in UTC and this is a single-operator tool."""
    cursor = await conn.execute(
        """
        SELECT
            SUM(COALESCE(estimated_cost, 0)) AS cost,
            SUM(COALESCE(prompt_tokens, 0)) AS prompt_tokens,
            SUM(COALESCE(completion_tokens, 0)) AS completion_tokens
        FROM request_stats
        WHERE timestamp >= datetime('now', 'start of day')
        """
    )
    row = await cursor.fetchone()
    await cursor.close()
    return {
        "cost_today": round(row["cost"] or 0.0, 6),
        "prompt_tokens_today": row["prompt_tokens"] or 0,
        "completion_tokens_today": row["completion_tokens"] or 0,
    }


async def cost_per_hour_buckets(conn: DatabaseConnection, hours: int = 24) -> list[float]:
    """Hourly cost sparkline for the trailing `hours` window (default: today-ish, 24h)."""
    cursor = await conn.execute(
        "SELECT timestamp, estimated_cost FROM request_stats WHERE timestamp > datetime('now', ?)",
        (f"-{hours} hours",),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    now = datetime.now(timezone.utc).timestamp()
    buckets = [0.0] * hours
    for row in rows:
        age = now - parse_str(row["timestamp"])
        if age < 0:
            continue
        idx = hours - 1 - int(age // 3600)
        if 0 <= idx < hours:
            buckets[idx] += row["estimated_cost"] or 0.0
    return [round(v, 6) for v in buckets]


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, int(round(pct / 100 * (len(sorted_values) - 1)))))
    return sorted_values[idx]


async def latency_percentiles(conn: DatabaseConnection, window_seconds: int, provider_id: str | None = None) -> dict:
    """P50/P95/P99 of model_response_ms across a time window — computed in
    The bounded sample is evaluated in Python to keep API behavior stable."""
    cursor = await conn.execute(
        """
        SELECT model_response_ms FROM request_stats
        WHERE model_response_ms IS NOT NULL AND timestamp > datetime('now', ?)
          AND (CAST(? AS TEXT) IS NULL OR provider_id = CAST(? AS TEXT))
        ORDER BY model_response_ms
        """,
        (f"-{window_seconds} seconds", provider_id, provider_id),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    values = [r["model_response_ms"] for r in rows]
    return {
        "p50": round(_percentile(values, 50)),
        "p95": round(_percentile(values, 95)),
        "p99": round(_percentile(values, 99)),
        "sample_size": len(values),
    }


async def token_usage_buckets(
    conn: DatabaseConnection, bucket_seconds: int, window_seconds: int, bucket_count: int,
    provider_id: str | None = None,
) -> dict:
    cursor = await conn.execute(
        """SELECT timestamp, prompt_tokens, completion_tokens FROM request_stats
           WHERE timestamp > datetime('now', ?) AND (CAST(? AS TEXT) IS NULL OR provider_id = CAST(? AS TEXT))""",
        (f"-{window_seconds} seconds", provider_id, provider_id),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    now = datetime.now(timezone.utc).timestamp()
    prompt = [0] * bucket_count
    completion = [0] * bucket_count
    for row in rows:
        age = now - parse_str(row["timestamp"])
        if age < 0:
            continue
        idx = bucket_count - 1 - int(age // bucket_seconds)
        if not (0 <= idx < bucket_count):
            continue
        prompt[idx] += row["prompt_tokens"] or 0
        completion[idx] += row["completion_tokens"] or 0
    return {"prompt": prompt, "completion": completion}


async def latency_buckets(
    conn: DatabaseConnection, bucket_seconds: int, window_seconds: int, bucket_count: int,
    provider_id: str | None = None,
) -> dict:
    """Per-bucket average queue wait vs. average model response time, so
    slow buckets can be attributed to throttling vs. the model itself."""
    cursor = await conn.execute(
        """SELECT timestamp, wait_ms, model_response_ms FROM request_stats
           WHERE timestamp > datetime('now', ?) AND (CAST(? AS TEXT) IS NULL OR provider_id = CAST(? AS TEXT))""",
        (f"-{window_seconds} seconds", provider_id, provider_id),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    now = datetime.now(timezone.utc).timestamp()
    wait_sum = [0] * bucket_count
    wait_count = [0] * bucket_count
    response_sum = [0] * bucket_count
    response_count = [0] * bucket_count
    for row in rows:
        age = now - parse_str(row["timestamp"])
        if age < 0:
            continue
        idx = bucket_count - 1 - int(age // bucket_seconds)
        if not (0 <= idx < bucket_count):
            continue
        wait_sum[idx] += row["wait_ms"] or 0
        wait_count[idx] += 1
        if row["model_response_ms"] is not None:
            response_sum[idx] += row["model_response_ms"]
            response_count[idx] += 1

    queue_wait_avg = [round(s / c) if c else 0 for s, c in zip(wait_sum, wait_count)]
    model_response_avg = [round(s / c) if c else 0 for s, c in zip(response_sum, response_count)]
    return {"queue_wait_avg": queue_wait_avg, "model_response_avg": model_response_avg}


TOKEN_HISTOGRAM_BINS = [0, 100, 250, 500, 1000, 2000, 5000]  # last bucket is "5000+"


async def tokens_histogram(conn: DatabaseConnection, window_seconds: int, provider_id: str | None = None) -> dict:
    """Distribution of total tokens (prompt+completion) per request, bucketed
    by size range — helps spot outlier/oversized requests at a glance."""
    cursor = await conn.execute(
        """
        SELECT COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0) AS total_tokens
        FROM request_stats
        WHERE (prompt_tokens IS NOT NULL OR completion_tokens IS NOT NULL) AND timestamp > datetime('now', ?)
          AND (CAST(? AS TEXT) IS NULL OR provider_id = CAST(? AS TEXT))
        """,
        (f"-{window_seconds} seconds", provider_id, provider_id),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    counts = [0] * len(TOKEN_HISTOGRAM_BINS)
    for row in rows:
        total = row["total_tokens"]
        idx = 0
        for i, edge in enumerate(TOKEN_HISTOGRAM_BINS):
            if total >= edge:
                idx = i
        counts[idx] += 1

    labels = []
    for i, edge in enumerate(TOKEN_HISTOGRAM_BINS):
        if i + 1 < len(TOKEN_HISTOGRAM_BINS):
            labels.append(f"{edge}-{TOKEN_HISTOGRAM_BINS[i + 1]}")
        else:
            labels.append(f"{edge}+")
    return {"labels": labels, "counts": counts}


ERROR_TYPES = ["rate_limited", "server_error", "client_error", "timeout", "unknown"]


async def error_breakdown(conn: DatabaseConnection, window_seconds: int, provider_id: str | None = None) -> dict:
    cursor = await conn.execute(
        """
        SELECT error_type, COUNT(*) AS c FROM request_stats
        WHERE error_type IS NOT NULL AND timestamp > datetime('now', ?)
          AND (CAST(? AS TEXT) IS NULL OR provider_id = CAST(? AS TEXT))
        GROUP BY error_type
        """,
        (f"-{window_seconds} seconds", provider_id, provider_id),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    counts = {t: 0 for t in ERROR_TYPES}
    for row in rows:
        if row["error_type"] in counts:
            counts[row["error_type"]] = row["c"]
    return counts


async def has_sustained_queueing(conn: DatabaseConnection, window_seconds: int) -> bool:
    """True if every queue_samples row in the trailing window shows queue_depth
    > 0 — i.e. the queue never drained for the whole window, not just a blip."""
    cursor = await conn.execute(
        """
        SELECT COUNT(*) AS total, SUM(CASE WHEN queue_depth = 0 THEN 1 ELSE 0 END) AS zero_count
        FROM queue_samples WHERE timestamp > datetime('now', ?)
        """,
        (f"-{window_seconds} seconds",),
    )
    row = await cursor.fetchone()
    await cursor.close()
    total = row["total"] or 0
    zero_count = row["zero_count"] or 0
    # Require close to full coverage of the window (sampler runs ~1/sec) so a
    # just-started proxy with only 2 samples can't trigger a false positive.
    return total >= max(1, window_seconds - 5) and zero_count == 0


async def upstream_all_failed_recent(conn: DatabaseConnection, n: int = 3) -> bool:
    cursor = await conn.execute(
        "SELECT status_code FROM request_stats ORDER BY id DESC LIMIT ?",
        (n,),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    if len(rows) < n:
        return False
    return all(status_label(r["status_code"]) == "error" for r in rows)


async def chart_status_breakdown(
    conn: DatabaseConnection, bucket_seconds: int, window_seconds: int, bucket_count: int,
    provider_id: str | None = None,
) -> dict:
    """Per-bucket request counts split into success / throttled / error, so the
    dashboard's request chart can show a stacked health breakdown instead of a
    raw total. "Throttled" covers both a 429 from upstream and a request that
    waited >2s for a token — matching the row-level color logic used elsewhere
    on the dashboard. Sourced from request_stats (not `requests`) since it's
    written on every request and — unlike `requests` — never falls short of
    data for older/longer ranges once payloads have aged out.
    """
    cursor = await conn.execute(
        """SELECT timestamp, wait_ms, status_code FROM request_stats
           WHERE timestamp > datetime('now', ?) AND (CAST(? AS TEXT) IS NULL OR provider_id = CAST(? AS TEXT))""",
        (f"-{window_seconds} seconds", provider_id, provider_id),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    now = datetime.now(timezone.utc).timestamp()
    success = [0] * bucket_count
    throttled = [0] * bucket_count
    error = [0] * bucket_count
    for row in rows:
        age = now - parse_str(row["timestamp"])
        if age < 0:
            continue
        idx = bucket_count - 1 - int(age // bucket_seconds)
        if not (0 <= idx < bucket_count):
            continue
        label = status_label(row["status_code"])
        if label == "error":
            error[idx] += 1
        elif label == "429" or (row["wait_ms"] or 0) > 2000:
            throttled[idx] += 1
        else:
            success[idx] += 1
    return {"success": success, "throttled": throttled, "error": error}


async def queue_sample_buckets(
    conn: DatabaseConnection, bucket_seconds: int, window_seconds: int, bucket_count: int,
    provider_id: str | None = None,
) -> dict:
    """Per-bucket queue depth (max, to preserve spikes even at coarse zoom
    levels) and token level (average, since it's a continuous trend rather
    than a spiky event) from the queue_samples history table.
    """
    cursor = await conn.execute(
        """SELECT timestamp, queue_depth, token_level FROM queue_samples
           WHERE timestamp > datetime('now', ?) AND (CAST(? AS TEXT) IS NULL OR provider_id = CAST(? AS TEXT))""",
        (f"-{window_seconds} seconds", provider_id, provider_id),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    now = datetime.now(timezone.utc).timestamp()
    depth_max = [0] * bucket_count
    token_sum = [0.0] * bucket_count
    token_count = [0] * bucket_count
    for row in rows:
        age = now - parse_str(row["timestamp"])
        if age < 0:
            continue
        idx = bucket_count - 1 - int(age // bucket_seconds)
        if not (0 <= idx < bucket_count):
            continue
        depth_max[idx] = max(depth_max[idx], row["queue_depth"])
        token_sum[idx] += row["token_level"]
        token_count[idx] += 1

    token_levels = []
    last_known = None
    for total, count in zip(token_sum, token_count):
        if count:
            last_known = total / count
        token_levels.append(last_known)
    first_known = next((v for v in token_levels if v is not None), 0.0)
    token_levels = [round(first_known if v is None else v, 3) for v in token_levels]

    return {"queue_depth": depth_max, "token_level": token_levels}


# --- Request Log: search / count / delete / export -------------------------
#
# All of these read from request_stats (the long-lived table) LEFT JOINed to
# requests (the payload-bearing, short-lived table) on the shared id. Once a
# `requests` row ages out past its retention window, the join simply returns
# NULL for the payload columns — callers show "payload no longer available"
# rather than erroring.


def _build_where(
    *,
    start_ts: str | None = None,
    end_ts: str | None = None,
    model: str | None = None,
    status: str | None = None,
    search: str | None = None,
    provider_id: str | None = None,
    before_id: int | None = None,
) -> tuple[str, list]:
    conditions = []
    params: list = []

    if before_id is not None:
        conditions.append("rs.id < ?")
        params.append(before_id)
    if start_ts:
        conditions.append("rs.timestamp >= ?")
        params.append(start_ts)
    if end_ts:
        conditions.append("rs.timestamp <= ?")
        params.append(end_ts)
    if model:
        conditions.append("rs.model = ?")
        params.append(model)
    if provider_id:
        conditions.append("rs.provider_id = ?")
        params.append(provider_id)
    if status in STATUS_CONDITIONS:
        conditions.append(STATUS_CONDITIONS[status])
    if search:
        conditions.append("r.request_payload LIKE ?")
        params.append(f"%{search}%")

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


def _row_to_search_dict(row: DatabaseRow) -> dict:
    request_payload_raw = row["request_payload"]
    response_payload_raw = row["response_payload"]
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "model": row["model"],
        "status_code": row["status_code"],
        "status": status_label(row["status_code"]),
        "wait_ms": row["wait_ms"],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "model_response_ms": row["model_response_ms"],
        "time_to_first_token_ms": row["time_to_first_token_ms"],
        "input_cost": row["input_cost"],
        "output_cost": row["output_cost"],
        "estimated_cost": row["estimated_cost"],
        "error_type": row["error_type"],
        "provider_id": row["provider_id"],
        "upstream_model": row["upstream_model"],
        "route_alias": row["route_alias"],
        "attempt_count": row["attempt_count"],
        "request_payload": json.loads(request_payload_raw) if request_payload_raw else None,
        "response_payload": json.loads(response_payload_raw) if response_payload_raw else None,
        "payload_available": request_payload_raw is not None or response_payload_raw is not None,
    }


SEARCH_SELECT = """
SELECT rs.id, rs.timestamp, rs.model, rs.status_code, rs.wait_ms, rs.prompt_tokens, rs.completion_tokens,
       rs.model_response_ms, rs.time_to_first_token_ms, rs.input_cost, rs.output_cost, rs.estimated_cost, rs.error_type,
       rs.provider_id, rs.upstream_model, rs.route_alias, rs.attempt_count,
       r.request_payload, r.response_payload
FROM request_stats rs
LEFT JOIN requests r ON r.id = rs.id
"""


async def search_requests(conn: DatabaseConnection, *, limit: int = 50, **filters) -> list[dict]:
    where, params = _build_where(**filters)
    query = SEARCH_SELECT + where + " ORDER BY rs.id DESC LIMIT ?"
    cursor = await conn.execute(query, [*params, limit])
    rows = await cursor.fetchall()
    await cursor.close()
    return [_row_to_search_dict(r) for r in rows]


async def count_matching(conn: DatabaseConnection, **filters) -> int:
    filters.pop("before_id", None)  # count reflects the whole filtered set, not a page
    where, params = _build_where(**filters)
    query = f"SELECT COUNT(*) AS c FROM request_stats rs LEFT JOIN requests r ON r.id = rs.id{where}"
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    await cursor.close()
    return row["c"]


async def find_matching_ids(conn: DatabaseConnection, **filters) -> list[int]:
    filters.pop("before_id", None)
    where, params = _build_where(**filters)
    query = f"SELECT rs.id AS id FROM request_stats rs LEFT JOIN requests r ON r.id = rs.id{where}"
    cursor = await conn.execute(query, params)
    rows = await cursor.fetchall()
    await cursor.close()
    return [row["id"] for row in rows]


async def delete_by_ids(conn: DatabaseConnection, ids: list[int]) -> None:
    chunk_size = 500
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i : i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        await conn.execute(f"DELETE FROM requests WHERE id IN ({placeholders})", chunk)
        await conn.execute(f"DELETE FROM request_stats WHERE id IN ({placeholders})", chunk)
    await conn.commit()


async def delete_request(conn: DatabaseConnection, request_id: int) -> bool:
    cursor = await conn.execute("DELETE FROM request_stats WHERE id = ?", (request_id,))
    deleted = cursor.rowcount > 0
    await conn.execute("DELETE FROM requests WHERE id = ?", (request_id,))
    await conn.commit()
    return deleted


async def count_user_roles(conn: DatabaseConnection) -> int:
    cursor = await conn.execute("SELECT COUNT(*) AS count FROM user_roles")
    row = await cursor.fetchone()
    return int(row["count"] if row else 0)


async def bootstrap_first_admin(conn: DatabaseConnection, *, user_id: str, email: str) -> dict | None:
    cursor = await conn.execute(
        """
        WITH gate AS MATERIALIZED (SELECT pg_advisory_xact_lock(8217062401)),
        created AS (
          INSERT INTO user_roles (user_id, email, role, disabled)
          SELECT CAST(CAST(? AS text) AS uuid), ?, 'admin', false
          FROM gate
          WHERE NOT EXISTS (SELECT 1 FROM user_roles)
          ON CONFLICT (user_id) DO NOTHING
          RETURNING user_id, email, role, disabled, created_at, updated_at
        )
        SELECT * FROM created
        """,
        (user_id, email.strip().lower()),
    )
    row = await cursor.fetchone()
    await conn.commit()
    return row


async def get_user_role(conn: DatabaseConnection, user_id: str) -> dict | None:
    cursor = await conn.execute(
        "SELECT user_id, email, role, disabled, created_at, updated_at FROM user_roles WHERE user_id = CAST(CAST(? AS text) AS uuid)",
        (user_id,),
    )
    return await cursor.fetchone()


async def list_user_roles(conn: DatabaseConnection) -> list[dict]:
    cursor = await conn.execute(
        "SELECT user_id, email, role, disabled, created_at, updated_at FROM user_roles ORDER BY lower(email)"
    )
    return await cursor.fetchall()


async def upsert_user_role(
    conn: DatabaseConnection, *, user_id: str, email: str, role: str, disabled: bool = False
) -> None:
    await conn.execute(
        """
        INSERT INTO user_roles (user_id, email, role, disabled)
        VALUES (CAST(CAST(? AS text) AS uuid), ?, ?, ?)
        ON CONFLICT (user_id) DO UPDATE SET
          email = EXCLUDED.email,
          role = EXCLUDED.role,
          disabled = EXCLUDED.disabled,
          updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, email.strip().lower(), role, disabled),
    )
    await conn.commit()


async def insert_proxy_api_key(
    conn: DatabaseConnection,
    *,
    key_id: str,
    name: str,
    prefix: str,
    digest: bytes,
    created_by: str,
    scopes: list[str],
    expires_at: str | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO proxy_api_keys (id, name, key_prefix, secret_digest, created_by, scopes, expires_at)
        VALUES (CAST(CAST(? AS text) AS uuid), ?, ?, ?, CAST(CAST(? AS text) AS uuid), ?, CAST(CAST(? AS text) AS timestamptz))
        """,
        (key_id, name, prefix, digest, created_by, scopes, expires_at),
    )
    await conn.commit()


async def list_proxy_api_keys(conn: DatabaseConnection) -> list[dict]:
    cursor = await conn.execute(
        """
        SELECT id, name, key_prefix, scopes, created_by, created_at, expires_at, revoked_at, last_used_at
        FROM proxy_api_keys ORDER BY created_at DESC
        """
    )
    rows = await cursor.fetchall()
    for row in rows:
        if isinstance(row.get("scopes"), str):
            value = row["scopes"]
            row["scopes"] = json.loads(value) if value.startswith("[") else [item for item in value.strip("{}").split(",") if item]
    return rows


async def get_proxy_api_key_by_prefix(conn: DatabaseConnection, prefix: str) -> dict | None:
    cursor = await conn.execute(
        """
        SELECT id, name, key_prefix, secret_digest, scopes, expires_at, revoked_at
        FROM proxy_api_keys WHERE key_prefix = ?
        """,
        (prefix,),
    )
    return await cursor.fetchone()


async def touch_proxy_api_key(conn: DatabaseConnection, key_id: str) -> None:
    await conn.execute("UPDATE proxy_api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE id = CAST(CAST(? AS text) AS uuid)", (key_id,))
    await conn.commit()


async def revoke_proxy_api_key(conn: DatabaseConnection, key_id: str) -> bool:
    cursor = await conn.execute(
        "UPDATE proxy_api_keys SET revoked_at = CURRENT_TIMESTAMP WHERE id = CAST(CAST(? AS text) AS uuid) AND revoked_at IS NULL",
        (key_id,),
    )
    await conn.commit()
    return cursor.rowcount > 0


async def insert_audit_event(
    conn: DatabaseConnection,
    *,
    actor_type: str,
    actor_id: str | None,
    action: str,
    outcome: str,
    target_type: str | None = None,
    target_id: str | None = None,
    source_ip: str | None = None,
    user_agent: str | None = None,
    details: dict | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO audit_events
          (actor_type, actor_id, action, target_type, target_id, outcome, source_ip, user_agent, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS jsonb))
        """,
        (
            actor_type,
            actor_id,
            action,
            target_type,
            target_id,
            outcome,
            source_ip,
            (user_agent or "")[:512],
            json.dumps(details or {}, separators=(",", ":")),
        ),
    )
    await conn.commit()


async def list_audit_events(conn: DatabaseConnection, *, limit: int = 100) -> list[dict]:
    cursor = await conn.execute(
        """
        SELECT id, occurred_at, actor_type, actor_id, action, target_type, target_id,
               outcome, source_ip, user_agent, details
        FROM audit_events ORDER BY id DESC LIMIT ?
        """,
        (min(max(limit, 1), 500),),
    )
    rows = await cursor.fetchall()
    for row in rows:
        if isinstance(row.get("details"), str):
            row["details"] = json.loads(row["details"])
    return rows
