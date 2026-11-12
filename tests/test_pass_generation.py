import base64
import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pydantic import ValidationError

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

    @patch.dict(
        "os.environ",
        {"PKPASS_SIGNING_MODE": "unsigned", "COMPANY_LOGO_PATH": ""},
    )
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

    def test_configured_company_logo_is_added_at_multiple_resolutions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logo_path = Path(temp_dir) / "company-logo.png"
            Image.new("RGB", (240, 60), "red").save(logo_path)

            with patch.dict(
                "os.environ",
                {
                    "PKPASS_SIGNING_MODE": "unsigned",
                    "COMPANY_LOGO_PATH": str(logo_path),
                },
            ):
                contents = build_pkpass(self.contact)

        with zipfile.ZipFile(BytesIO(contents)) as package:
            self.assertTrue(
                {"logo.png", "logo@2x.png", "logo@3x.png"}.issubset(
                    package.namelist()
                )
            )
            for name, expected_size in (
                ("logo.png", (160, 40)),
                ("logo@2x.png", (320, 80)),
                ("logo@3x.png", (480, 120)),
            ):
                with self.subTest(logo=name):
                    with Image.open(BytesIO(package.read(name))) as image:
                        self.assertEqual(image.size, expected_size)

    @patch.dict(
        "os.environ",
        {"PKPASS_SIGNING_MODE": "unsigned", "COMPANY_LOGO_PATH": ""},
    )
    def test_profile_picture_is_added_as_wallet_thumbnail_variants(self) -> None:
        image_bytes = BytesIO()
        Image.new("RGB", (120, 80), "purple").save(image_bytes, format="PNG")
        contact = ContactRequest(
            **self.contact.model_dump(exclude_none=True),
            profilePicture=base64.b64encode(image_bytes.getvalue()).decode("ascii"),
        )

        contents = build_pkpass(contact)

        with zipfile.ZipFile(BytesIO(contents)) as package:
            for name, expected_size in (
                ("thumbnail.png", (90, 90)),
                ("thumbnail@2x.png", (180, 180)),
                ("thumbnail@3x.png", (270, 270)),
            ):
                with self.subTest(thumbnail=name):
                    self.assertIn(name, package.namelist())
                    with Image.open(BytesIO(package.read(name))) as image:
                        self.assertEqual(image.size, expected_size)
                        self.assertEqual(image.getpixel((45, 45))[:3], (128, 0, 128))

    def test_invalid_profile_picture_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "valid image"):
            ContactRequest(
                **self.contact.model_dump(exclude_none=True),
                profile_picture=base64.b64encode(b"not an image"),
            )

    def test_oversized_profile_picture_is_rejected(self) -> None:
        oversized_image = base64.b64encode(b"x" * 5_000_001)
        with self.assertRaisesRegex(ValidationError, "must not exceed 5 MB"):
            ContactRequest(
                **self.contact.model_dump(exclude_none=True),
                profile_picture=oversized_image,
            )

    @patch.dict(
        "os.environ",
        {
            "PKPASS_SIGNING_MODE": "unsigned",
            "COMPANY_LOGO_PATH": "/missing/company-logo.png",
        },
    )
    def test_invalid_company_logo_path_is_reported(self) -> None:
        with self.assertRaisesRegex(ValueError, "COMPANY_LOGO_PATH"):
            build_pkpass(self.contact)

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
