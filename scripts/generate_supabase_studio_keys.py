from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path


def _segment(value: dict[str, object]) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def create_legacy_key(secret: str, issuer: str, role: str, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    header = _segment({"alg": "HS256", "typ": "JWT"})
    payload = _segment({
        "exp": issued_at + (10 * 365 * 24 * 60 * 60),
        "iat": issued_at - 60,
        "iss": issuer,
        "role": role,
    })
    signing_input = f"{header}.{payload}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{signing_input}.{encoded_signature}"


def write_keys(target: Path, secret: str, issuer: str) -> None:
    if len(secret) < 32:
        raise ValueError("SUPABASE_JWT_SECRET must contain at least 32 characters")
    target.mkdir(parents=True, exist_ok=True)
    values = {
        "anon-key": create_legacy_key(secret, issuer, "anon"),
        "service-role-key": create_legacy_key(secret, issuer, "service_role"),
    }
    for filename, value in values.items():
        temporary = target / f".{filename}.tmp"
        temporary.write_text(value, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(target / filename)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate_supabase_studio_keys.py TARGET_DIRECTORY")
    write_keys(
        Path(sys.argv[1]),
        os.environ.get("SUPABASE_JWT_SECRET", ""),
        os.environ.get("SUPABASE_JWT_ISSUER", "prismux"),
    )


if __name__ == "__main__":
    main()
