import json
import unittest
import zipfile
from io import BytesIO
from unittest.mock import patch

from app.main import ContactRequest, build_pkpass


class PassGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contact = ContactRequest(
            name="Ada Lovelace",
            academicDegree="PhD",
            jobPosition="Researcher",
            email="ada@example.org",
            phoneNumber="+1 555 0100",
            department="Computing",
            location="London",
        )

    @patch.dict("os.environ", {"PKPASS_SIGNING_MODE": "unsigned"})
    def test_unsigned_package_contains_contact_data_and_qr(self) -> None:
        contents = build_pkpass(self.contact)
        with zipfile.ZipFile(BytesIO(contents)) as package:
            self.assertEqual(
                set(package.namelist()),
                {"pass.json", "icon.png", "icon@2x.png", "qr.png"},
            )
            pass_data = json.loads(package.read("pass.json"))
            serialized_pass = json.dumps(pass_data)
            for value in (
                "Ada Lovelace",
                "PhD",
                "Researcher",
                "ada@example.org",
                "+1 555 0100",
                "Computing",
                "London",
            ):
                self.assertIn(value, serialized_pass)
            self.assertEqual(
                pass_data["barcodes"][0]["format"],
                "PKBarcodeFormatQR",
            )
            self.assertIn("BEGIN:VCARD", pass_data["barcodes"][0]["message"])
            self.assertGreater(len(package.read("qr.png")), 0)

    @patch.dict(
        "os.environ",
        {
            "PKPASS_SIGNING_MODE": "unsigned",
            "PASS_TYPE_IDENTIFIER": "",
            "TEAM_IDENTIFIER": "",
        },
    )
    def test_unsigned_package_has_nonempty_apple_identifiers(self) -> None:
        contents = build_pkpass(self.contact)
        with zipfile.ZipFile(BytesIO(contents)) as package:
            pass_data = json.loads(package.read("pass.json"))

        self.assertEqual(
            pass_data["passTypeIdentifier"],
            "pass.com.example.contact",
        )
        self.assertEqual(pass_data["teamIdentifier"], "TEAMID1234")

    @patch.dict("os.environ", {"PKPASS_SIGNING_MODE": "invalid"})
    def test_unknown_signing_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_pkpass(self.contact)

    @patch.dict("os.environ", {"PKPASS_SIGNING_MODE": "signed"}, clear=True)
    def test_signed_mode_requires_apple_identifiers(self) -> None:
        with self.assertRaisesRegex(ValueError, "PASS_TYPE_IDENTIFIER"):
            build_pkpass(self.contact)


if __name__ == "__main__":
    unittest.main()
