import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import nuclio_function


class FakeContext:
    @staticmethod
    def Response(body, headers, content_type, status_code):
        return SimpleNamespace(
            body=body,
            headers=headers,
            content_type=content_type,
            status_code=status_code,
        )


class NuclioFunctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = FakeContext()
        self.contact_body = {
            "name": "Ada Lovelace",
            "academicDegree": "PhD",
            "jobPosition": "Researcher",
            "email": "ada@example.org",
            "phoneNumber": "+1 555 0100",
            "department": "Computing",
            "location": "London",
        }

    def event(self, method="POST", path="/passes", body=None, headers=None):
        return SimpleNamespace(
            method=method,
            path=path,
            body=self.contact_body if body is None else body,
            headers=headers or {},
        )

    def test_health_is_public(self) -> None:
        response = nuclio_function.handler(
            self.context,
            self.event(method="GET", path="/health", headers={}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"status": "ok"})

    @patch.dict("os.environ", {"API_KEY": "expected"})
    def test_protected_route_rejects_missing_api_key(self) -> None:
        response = nuclio_function.handler(self.context, self.event())
        self.assertEqual(response.status_code, 401)

    @patch.dict("os.environ", {"API_KEY": "expected"})
    @patch("nuclio_function.build_pkpass", return_value=b"pkpass-bytes")
    def test_apple_pass_returns_binary_package(self, build_pass) -> None:
        response = nuclio_function.handler(
            self.context,
            self.event(headers={"x-api-key": "expected"}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/vnd.apple.pkpass")
        self.assertEqual(response.body, b"pkpass-bytes")
        build_pass.assert_called_once()

    @patch.dict("os.environ", {"API_KEY": "expected"})
    @patch(
        "nuclio_function.build_google_wallet_save_url",
        return_value=("https://pay.google.com/gp/v/save/token", "123.object"),
    )
    def test_google_wallet_route_returns_save_link(self, build_save_url) -> None:
        response = nuclio_function.handler(
            self.context,
            self.event(
                path="/passes/google-wallet",
                body=json.dumps(self.contact_body).encode("utf-8"),
                headers={"X-API-Key": "expected"},
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.body),
            {
                "saveUrl": "https://pay.google.com/gp/v/save/token",
                "objectId": "123.object",
            },
        )
        build_save_url.assert_called_once()

    @patch.dict("os.environ", {"API_KEY": "expected"})
    def test_invalid_request_body_returns_bad_request(self) -> None:
        response = nuclio_function.handler(
            self.context,
            self.event(body="{invalid", headers={"X-API-Key": "expected"}),
        )
        self.assertEqual(response.status_code, 400)

    @patch.dict("os.environ", {"API_KEY": "expected"})
    def test_invalid_contact_returns_validation_error(self) -> None:
        response = nuclio_function.handler(
            self.context,
            self.event(body={}, headers={"X-API-Key": "expected"}),
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
