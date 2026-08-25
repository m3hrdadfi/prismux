import base64
import hashlib
import hmac
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "generate_supabase_studio_keys.py"
SPEC = importlib.util.spec_from_file_location("studio_keys", SCRIPT_PATH)
studio_keys = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(studio_keys)


def decode_segment(value: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))


class StudioKeyTests(unittest.TestCase):
    def test_generated_service_key_has_expected_claims_and_signature(self):
        secret = "a-secure-test-secret-that-is-longer-than-32-characters"
        token = studio_keys.create_legacy_key(secret, "prismux", "service_role", now=1_800_000_000)
        header, payload, signature = token.split(".")

        self.assertEqual(decode_segment(payload)["role"], "service_role")
        self.assertEqual(decode_segment(payload)["iss"], "prismux")
        expected = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        self.assertEqual(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)), expected)

    def test_keys_are_written_without_exposing_group_or_world_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            studio_keys.write_keys(target, "b" * 40, "prismux")

            self.assertTrue((target / "anon-key").read_text().startswith("ey"))
            self.assertEqual((target / "service-role-key").stat().st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
