import hashlib
import hmac
import ipaddress
import json
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import jwt
from fastapi import Request

from app import db
from app.config import Settings


VALID_ROLES = {"viewer", "operator", "admin"}
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
MACHINE_KEY_SCOPE = "proxy:invoke"


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    actor_type: str
    actor_id: str
    email: str | None = None
    role: str | None = None
    scopes: tuple[str, ...] = ()


class LoginThrottle:
    def __init__(self, attempts: int, window_seconds: int):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, key: str, now: float) -> deque[float]:
        events = self._events[key]
        while events and events[0] <= now - self.window_seconds:
            events.popleft()
        if not events:
            self._events.pop(key, None)
            events = self._events[key]
        return events

    def allowed(self, key: str) -> bool:
        return len(self._trim(key, time.monotonic())) < self.attempts

    def failure(self, key: str) -> None:
        self._trim(key, time.monotonic()).append(time.monotonic())

    def success(self, key: str) -> None:
        self._events.pop(key, None)


class SupabaseAuthClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.supabase_auth_url.rstrip("/"),
            follow_redirects=False,
            timeout=httpx.Timeout(15, connect=5),
        )

    def _headers(self, *, service: bool = False, token: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif service:
            now = int(time.time())
            service_token = jwt.encode(
                {
                    "iss": self.settings.supabase_jwt_issuer.rstrip("/"),
                    "role": "service_role",
                    "iat": now,
                    "exp": now + 300,
                },
                self.settings.supabase_jwt_secret,
                algorithm="HS256",
            )
            headers["apikey"] = service_token
            headers["Authorization"] = f"Bearer {service_token}"
        return headers

    async def password_login(self, email: str, password: str) -> dict[str, Any]:
        response = await self.client.post(
            "/token?grant_type=password",
            headers=self._headers(),
            json={"email": email, "password": password},
        )
        if response.status_code != 200:
            raise AuthenticationError("Invalid email or password")
        return response.json()

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        response = await self.client.post(
            "/token?grant_type=refresh_token",
            headers=self._headers(),
            json={"refresh_token": refresh_token},
        )
        if response.status_code != 200:
            raise AuthenticationError("Session could not be refreshed")
        return response.json()

    async def logout(self, access_token: str) -> None:
        response = await self.client.post("/logout", headers=self._headers(token=access_token))
        if response.status_code not in {200, 204, 401}:
            raise AuthenticationError("Session could not be revoked")

    async def list_users(self, *, page: int = 1, per_page: int = 100) -> dict[str, Any]:
        response = await self.client.get(
            "/admin/users", params={"page": page, "per_page": per_page}, headers=self._headers(service=True)
        )
        response.raise_for_status()
        return response.json()

    async def create_user(self, email: str, password: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"email": email, "email_confirm": bool(password)}
        if password:
            payload["password"] = password
            response = await self.client.post("/admin/users", json=payload, headers=self._headers(service=True))
        else:
            response = await self.client.post("/invite", json={"email": email}, headers=self._headers(service=True))
        response.raise_for_status()
        return response.json()

    async def ensure_bootstrap_user(self, email: str, password: str) -> None:
        response = await self.list_users()
        users = response.get("users", []) if isinstance(response, dict) else response
        if any(str(user.get("email", "")).lower() == email.lower() for user in users):
            return
        await self.create_user(email, password)

    async def close(self) -> None:
        await self.client.aclose()


class SecurityService:
    def __init__(self, settings: Settings, auth_client: SupabaseAuthClient):
        self.settings = settings
        self.auth_client = auth_client
        self.throttle = LoginThrottle(settings.auth_login_attempts, settings.auth_login_window_seconds)
        self.trusted_proxies = tuple(
            ipaddress.ip_network(item.strip(), strict=False)
            for item in settings.trusted_proxy_cidrs.split(",")
            if item.strip()
        )

    def validate_access_token(self, token: str) -> dict[str, Any]:
        if not self.settings.supabase_jwt_secret:
            raise AuthenticationError("Authentication is not configured")
        options: dict[str, Any] = {"require": ["exp", "sub", "aud"]}
        kwargs: dict[str, Any] = {"audience": "authenticated", "options": options}
        if self.settings.supabase_jwt_issuer:
            kwargs["issuer"] = self.settings.supabase_jwt_issuer.rstrip("/")
        try:
            return jwt.decode(token, self.settings.supabase_jwt_secret, algorithms=["HS256"], **kwargs)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Session is invalid or expired") from exc

    async def user_principal(self, database, token: str) -> Principal:
        claims = self.validate_access_token(token)
        role = await db.get_user_role(database, claims["sub"])
        if not role or role["disabled"]:
            raise AuthenticationError("Account access is disabled")
        return Principal("user", claims["sub"], claims.get("email") or role["email"], role["role"])

    @staticmethod
    def _machine_parts(value: str) -> tuple[str, str] | None:
        if not value.startswith(("prismux_live_", "rlp_live_")):
            return None
        parts = value.split("_", 3)
        if len(parts) != 4 or not parts[2] or len(parts[3]) < 24:
            return None
        return parts[2], parts[3]

    def machine_digest(self, raw_key: str) -> bytes:
        if not self.settings.api_key_pepper:
            raise AuthenticationError("Machine API key verification is not configured")
        return hmac.new(self.settings.api_key_pepper.encode(), raw_key.encode(), hashlib.sha256).digest()

    async def machine_principal(self, database, raw_key: str) -> Principal:
        parts = self._machine_parts(raw_key)
        if not parts:
            raise AuthenticationError("Invalid machine API key")
        prefix, _ = parts
        record = await db.get_proxy_api_key_by_prefix(database, prefix)
        if not record or record["revoked_at"]:
            raise AuthenticationError("Invalid machine API key")
        if record["expires_at"]:
            expires = datetime.fromisoformat(str(record["expires_at"]).replace("Z", "+00:00"))
            if expires <= datetime.now(timezone.utc):
                raise AuthenticationError("Invalid machine API key")
        if not hmac.compare_digest(bytes(record["secret_digest"]), self.machine_digest(raw_key)):
            raise AuthenticationError("Invalid machine API key")
        scopes = record["scopes"]
        if isinstance(scopes, str):
            scopes = json.loads(scopes) if scopes.startswith("[") else [item for item in scopes.strip("{}").split(",") if item]
        if MACHINE_KEY_SCOPE not in scopes:
            raise AuthorizationError("Machine API key lacks proxy:invoke scope")
        await db.touch_proxy_api_key(database, str(record["id"]))
        return Principal("api_key", str(record["id"]), scopes=tuple(scopes))

    def create_machine_key(self) -> tuple[str, str, bytes]:
        prefix = secrets.token_hex(5)
        raw_key = f"prismux_live_{prefix}_{secrets.token_urlsafe(32)}"
        return raw_key, prefix, self.machine_digest(raw_key)

    def client_ip(self, request: Request) -> str | None:
        direct = request.client.host if request.client else None
        if not direct:
            return None
        try:
            direct_address = ipaddress.ip_address(direct)
            trusted = any(direct_address in network for network in self.trusted_proxies)
        except ValueError:
            return None
        if trusted:
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            if forwarded:
                try:
                    return str(ipaddress.ip_address(forwarded))
                except ValueError:
                    pass
        return str(direct_address)


def required_role(method: str, path: str) -> str:
    if path == "/api/auth/logout":
        return "viewer"
    if path.startswith("/api/access") or path == "/reset" or method == "DELETE":
        return "admin"
    if method in {"POST", "PUT", "PATCH"}:
        return "operator"
    return "viewer"


def authorize_role(principal: Principal, required: str) -> None:
    if principal.role not in ROLE_RANK or ROLE_RANK[principal.role] < ROLE_RANK[required]:
        raise AuthorizationError(f"{required.capitalize()} access is required")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def new_key_id() -> str:
    return str(uuid.uuid4())
