SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "auth_token", "access_token", "bearer"}


def sanitize(value):
    """Recursively redact sensitive keys from a JSON-like structure before it's
    stored or shown on the dashboard. Request/response headers (which carry the
    real Authorization header) are never captured in the first place — this only
    guards against a stray api_key-shaped field inside a request/response body."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else sanitize(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value
