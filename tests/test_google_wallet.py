import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.main import (
    ContactRequest,
    GoogleWalletLinkTooLongError,
    GoogleWalletSaveResponse,
    build_google_wallet_save_url,
)


class GoogleWalletTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contact = ContactRequest(
            givenName="Ada",
            surname="Lovelace",
            academicDegree="PhD",
            jobPosition="Researcher",
            email="ada@example.org",
            phoneNumber="+1 555 0100",
            department="Computing",
            street="1 Main St",
            postalCode="SW1A 1AA",
            city="London",
            country="UK",
        )
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_key = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        self.public_key = key.public_key()
        credentials_path = Path(self.temp_dir.name) / "service-account.json"
        credentials_path.write_text(
            json.dumps(
                {
                    "client_email": "wallet-service@example.iam.gserviceaccount.com",
                    "private_key": self.private_key,
                }
            ),
            encoding="utf-8",
        )
        self.environment = patch.dict(
            "os.environ",
            {
                "GOOGLE_WALLET_ISSUER_ID": "123456789",
                "GOOGLE_WALLET_CLASS_ID": "123456789.contact",
                "GOOGLE_WALLET_SERVICE_ACCOUNT_JSON": str(credentials_path),
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_builds_signed_google_wallet_link_with_contact_data(self) -> None:
        save_url, object_id = build_google_wallet_save_url(self.contact)
        token = save_url.rsplit("/", 1)[-1]
        claims = jwt.decode(
            token,
            self.public_key,
            algorithms=["RS256"],
            audience="google",
        )

        self.assertTrue(save_url.startswith("https://pay.google.com/gp/v/save/"))
        self.assertLessEqual(len(token), 1800)
        self.assertEqual(object_id, claims["payload"]["genericObjects"][0]["id"])
        generic_object = claims["payload"]["genericObjects"][0]
        self.assertEqual(generic_object["classId"], "123456789.contact")
        self.assertEqual(
            generic_object["header"]["defaultValue"]["value"], "PhD Ada Lovelace"
        )
        self.assertEqual(
            generic_object["barcode"]["type"],
            "QR_CODE",
        )
        self.assertIn("BEGIN:VCARD", generic_object["barcode"]["value"])
        self.assertIn("TITLE:Researcher\r\n", generic_object["barcode"]["value"])
        body = generic_object["textModulesData"][0]["body"]
        for value in ("ada@example.org", "+1 555 0100", "1 Main St", "London"):
            self.assertIn(value, body)
        self.assertNotIn("Computing", body)
        self.assertEqual(
            generic_object["subheader"]["defaultValue"]["value"],
            "Researcher",
        )

    def test_omitted_job_position_is_not_in_google_pass_or_vcard(self) -> None:
        payload = self.contact.model_dump(exclude={"job_position"})
        for extra in ({}, {"jobPosition": None}):
            with self.subTest(extra=extra):
                contact = ContactRequest.model_validate({**payload, **extra})
                save_url, _ = build_google_wallet_save_url(contact)
                claims = jwt.decode(
                    save_url.rsplit("/", 1)[-1],
                    self.public_key,
                    algorithms=["RS256"],
                    audience="google",
                )
                generic_object = claims["payload"]["genericObjects"][0]
                self.assertNotIn("subheader", generic_object)
                self.assertNotIn("\r\nTITLE:", generic_object["barcode"]["value"])
                self.assertIn("ADR;TYPE=WORK:", generic_object["barcode"]["value"])

    def test_apple_position_toggle_does_not_change_google_pass_or_vcard(self) -> None:
        payload = self.contact.model_dump(exclude={"show_job_position"})
        contact = ContactRequest.model_validate({**payload, "showJobPosition": False})
        save_url, _ = build_google_wallet_save_url(contact)
        claims = jwt.decode(
            save_url.rsplit("/", 1)[-1],
            self.public_key,
            algorithms=["RS256"],
            audience="google",
        )
        generic_object = claims["payload"]["genericObjects"][0]
        self.assertEqual(
            generic_object["subheader"]["defaultValue"]["value"], "Researcher"
        )
        self.assertIn("\r\nTITLE:Researcher\r\n", generic_object["barcode"]["value"])

    @patch.dict("os.environ", {"GOOGLE_WALLET_BACKGROUND_COLOR": "#a1b2c3"})
    def test_uses_configured_google_wallet_background_color(self) -> None:
        save_url, _ = build_google_wallet_save_url(self.contact)
        token = save_url.rsplit("/", 1)[-1]
        claims = jwt.decode(
            token,
            self.public_key,
            algorithms=["RS256"],
            audience="google",
        )
        generic_object = claims["payload"]["genericObjects"][0]

        self.assertEqual(generic_object["hexBackgroundColor"], "#a1b2c3")

    def test_response_serializes_camel_case_field_names(self) -> None:
        response = GoogleWalletSaveResponse(
            save_url="https://pay.google.com/gp/v/save/token",
            object_id="123456789.object",
        )
        self.assertEqual(
            response.model_dump(by_alias=True),
            {
                "saveUrl": "https://pay.google.com/gp/v/save/token",
                "objectId": "123456789.object",
            },
        )

    @patch.dict("os.environ", {"GOOGLE_WALLET_CLASS_ID": "other-issuer.contact"})
    def test_rejects_class_from_different_issuer(self) -> None:
        with self.assertRaisesRegex(ValueError, "must belong"):
            build_google_wallet_save_url(self.contact)

    def test_rejects_save_url_over_google_limit(self) -> None:
        long_contact = ContactRequest(
            givenName="N" * 256,
            surname="S" * 256,
            academicDegree="D" * 256,
            jobPosition="P" * 256,
            email="ada@example.org",
            phoneNumber="1" * 64,
            department="A" * 256,
            street="S" * 256,
            postalCode="1" * 32,
            city="C" * 256,
            country="K" * 256,
        )
        with self.assertRaises(GoogleWalletLinkTooLongError):
            build_google_wallet_save_url(long_contact)


if __name__ == "__main__":
    unittest.main()
