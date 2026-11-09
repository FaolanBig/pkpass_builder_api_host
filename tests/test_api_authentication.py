import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.main import verify_api_key


class ApiAuthenticationTests(unittest.TestCase):
    @patch.dict("os.environ", {"API_KEY": "expected-secret"})
    def test_accepts_matching_api_key(self) -> None:
        self.assertIsNone(verify_api_key("expected-secret"))

    @patch.dict("os.environ", {"API_KEY": "expected-secret"})
    def test_rejects_missing_api_key(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            verify_api_key(None)
        self.assertEqual(raised.exception.status_code, 401)

    @patch.dict("os.environ", {"API_KEY": "expected-secret"})
    def test_rejects_incorrect_api_key(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            verify_api_key("incorrect-secret")
        self.assertEqual(raised.exception.status_code, 401)

    @patch.dict("os.environ", {"API_KEY": "expected-secret"})
    def test_rejects_non_ascii_api_key(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            verify_api_key("incorrect-sëcret")
        self.assertEqual(raised.exception.status_code, 401)

    @patch.dict("os.environ", {}, clear=True)
    def test_reports_missing_server_configuration(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            verify_api_key("any-key")
        self.assertEqual(raised.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
