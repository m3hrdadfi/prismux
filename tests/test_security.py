import time
import unittest
from unittest.mock import AsyncMock, patch

import jwt

from app.auth import (
    AuthenticationError,
    AuthorizationError,
    LoginThrottle,
    Principal,
    SecurityService,
    SupabaseAuthClient,
    authorize_role,
    required_role,
)
from app.config import Settings
from app.multi_provider import ProviderConfig
from app.outbound import OutboundPolicy, OutboundPolicyError, ProtectedAsyncClient


def security_settings(**overrides):
    values = {
        "SUPABASE_JWT_SECRET": "a-secure-test-secret-that-is-long-enough",
        "SUPABASE_JWT_ISSUER": "https://proxy.test/auth/v1",
        "API_KEY_PEPPER": "independent-machine-key-pepper",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class OutboundPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_https_literal_is_allowed_and_pinned(self):
        decision = await OutboundPolicy().inspect("https://8.8.8.8/v1/models")
        self.assertEqual(decision.classification, "public")
        self.assertEqual(decision.pinned_url, "https://8.8.8.8/v1/models")

    async def test_metadata_destinations_remain_permanently_denied(self):
        policy = OutboundPolicy()
        for url in (
            "http://169.254.169.254/latest/meta-data",
            "http://metadata.google.internal/latest",
        ):
            with self.subTest(url=url), self.assertRaises(OutboundPolicyError):
                await policy.inspect(url)

    async def test_private_http_and_arbitrary_ports_are_allowed_by_default(self):
        policy = OutboundPolicy()
        decision = await policy.inspect("http://127.0.0.1:11434/v1/models")
        self.assertEqual(decision.classification, "private")

    async def test_disallowed_hosts_cidrs_and_ports_are_denied(self):
        policies_and_urls = (
            (OutboundPolicy(disallowed_hosts="models.internal:11434"), "http://models.internal:11434/v1/models"),
            (OutboundPolicy(disallowed_cidrs="100.64.0.0/10"), "http://100.121.70.76:11434/v1/models"),
            (OutboundPolicy(disallowed_ports="11434"), "http://127.0.0.1:11434/v1/models"),
        )
        for policy, url in policies_and_urls:
            with self.subTest(url=url), self.assertRaises(OutboundPolicyError):
                await policy.inspect(url)

    async def test_public_http_is_allowed_when_not_explicitly_blocked(self):
        decision = await OutboundPolicy().inspect("http://8.8.8.8/v1/models")
        self.assertEqual(decision.classification, "public")

    async def test_userinfo_and_fragments_are_denied(self):
        policy = OutboundPolicy()
        for url in (
            "https://user:password@8.8.8.8/v1",
            "https://8.8.8.8/v1#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(OutboundPolicyError):
                await policy.inspect(url)

    def test_models_url_must_share_provider_origin(self):
        with self.assertRaises(ValueError):
            ProviderConfig(id="blocked", name="Blocked", base_url="https://api.example/v1", models_url="https://evil.example/models")

    async def test_pinned_https_request_keeps_text_sni_hostname(self):
        client = ProtectedAsyncClient(OutboundPolicy())
        try:
            decision, headers, extensions = await client._prepare("https://8.8.8.8/v1/models", {"Accept": "application/json"})
        finally:
            await client.aclose()

        self.assertEqual(decision.pinned_url, "https://8.8.8.8/v1/models")
        self.assertEqual(headers["Host"], "8.8.8.8")
        self.assertEqual(extensions["sni_hostname"], "8.8.8.8")
        self.assertIsInstance(extensions["sni_hostname"], str)


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = security_settings()
        self.client = SupabaseAuthClient(self.settings)
        self.security = SecurityService(self.settings, self.client)

    async def asyncTearDown(self):
        await self.client.close()

    def token(self, **claims):
        now = int(time.time())
        payload = {
            "sub": "8a5334b4-03ab-45dd-bbb0-5ea3105c63a5",
            "email": "admin@example.com",
            "aud": "authenticated",
            "iss": self.settings.supabase_jwt_issuer,
            "iat": now,
            "exp": now + 60,
            **claims,
        }
        return jwt.encode(payload, self.settings.supabase_jwt_secret, algorithm="HS256")

    def test_jwt_signature_issuer_audience_and_expiry_are_validated(self):
        self.assertEqual(self.security.validate_access_token(self.token())["email"], "admin@example.com")
        for token in (
            self.token(iss="https://attacker.test"),
            self.token(aud="other"),
            self.token(exp=int(time.time()) - 1),
            jwt.encode(
                {"sub": "x", "aud": "authenticated", "exp": int(time.time()) + 60},
                "wrong-secret-that-is-still-long-enough-for-hs256",
                algorithm="HS256",
            ),
        ):
            with self.assertRaises(AuthenticationError):
                self.security.validate_access_token(token)

    async def test_machine_key_is_hmac_verified_and_revocable(self):
        raw_key, prefix, digest = self.security.create_machine_key()
        record = {
            "id": "6ea96244-41dd-4c1c-b019-5168fd24e602",
            "secret_digest": digest,
            "scopes": '["proxy:invoke"]',
            "expires_at": None,
            "revoked_at": None,
        }
        with patch("app.auth.db.get_proxy_api_key_by_prefix", AsyncMock(return_value=record)), patch("app.auth.db.touch_proxy_api_key", AsyncMock()) as touch:
            principal = await self.security.machine_principal(object(), raw_key)
            self.assertEqual(principal.actor_type, "api_key")
            self.assertEqual(prefix, raw_key.split("_")[2])
            touch.assert_awaited_once()
        record["revoked_at"] = "2026-01-01T00:00:00+00:00"
        with patch("app.auth.db.get_proxy_api_key_by_prefix", AsyncMock(return_value=record)):
            with self.assertRaises(AuthenticationError):
                await self.security.machine_principal(object(), raw_key)

    def test_role_matrix_keeps_destructive_and_access_actions_admin_only(self):
        self.assertEqual(required_role("GET", "/stats"), "viewer")
        self.assertEqual(required_role("PUT", "/api/settings"), "operator")
        self.assertEqual(required_role("DELETE", "/api/requests/1"), "admin")
        self.assertEqual(required_role("GET", "/api/access/audit"), "admin")
        authorize_role(Principal("user", "1", role="admin"), "operator")
        with self.assertRaises(AuthorizationError):
            authorize_role(Principal("user", "1", role="viewer"), "operator")

    def test_login_throttle_recovers_after_success(self):
        throttle = LoginThrottle(2, 300)
        throttle.failure("client")
        throttle.failure("client")
        self.assertFalse(throttle.allowed("client"))
        throttle.success("client")
        self.assertTrue(throttle.allowed("client"))
