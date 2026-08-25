import unittest

from app.database import _bind


class PostgreSQLBindingTests(unittest.TestCase):
    def test_optional_provider_filter_casts_null_parameters(self):
        statement, values = _bind(
            "SELECT 1 WHERE CAST(? AS TEXT) IS NULL OR provider_id = CAST(? AS TEXT)",
            (None, None),
        )

        self.assertIn("CAST(:p0 AS TEXT) IS NULL", statement)
        self.assertIn("provider_id = CAST(:p1 AS TEXT)", statement)
        self.assertEqual(values, {"p0": None, "p1": None})


if __name__ == "__main__":
    unittest.main()
