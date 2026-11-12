import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import time
import uuid
import zipfile
from enum import Enum
from io import BytesIO
from pathlib import Path

import qrcode
import jwt
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.security import APIKeyHeader
from pydantic import AliasChoices, Base64Bytes, BaseModel, ConfigDict, EmailStr, Field
from pydantic import field_validator
from PIL import Image, ImageDraw, ImageFont, ImageOps


class SigningMode(str, Enum):
    signed = "signed"
    unsigned = "unsigned"


class ContactRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    given_name: str = Field(
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices(
            "givenName", "given_name", "Given Name", "firstName", "first_name"
        ),
    )
    surname: str = Field(
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices("surname", "Surname", "lastName", "last_name"),
    )
    academic_degree: str = Field(
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices(
            "academicDegree", "academic_degree", "Academic Degree"
        ),
    )
    job_position: str = Field(
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices("jobPosition", "job_position", "Job Position"),
    )
    email: EmailStr
    phone_number: str = Field(
        min_length=1,
        max_length=64,
        validation_alias=AliasChoices("phoneNumber", "phone_number", "Phone Number"),
    )
    department: str = Field(min_length=1, max_length=256)
    street: str = Field(min_length=1, max_length=256)
    postal_code: str = Field(
        min_length=1,
        max_length=32,
        validation_alias=AliasChoices("postalCode", "postal_code", "postal-code"),
    )
    city: str = Field(min_length=1, max_length=256)
    country: str = Field(min_length=1, max_length=256)
    profile_picture: Base64Bytes | None = Field(
        default=None,
        max_length=6_666_668,
        validation_alias=AliasChoices("profilePicture", "profile_picture"),
    )

    @field_validator("profile_picture")
    @classmethod
    def validate_profile_picture(cls, image_bytes: bytes | None) -> bytes | None:
        if image_bytes is None:
            return None

        if len(image_bytes) > 5_000_000:
            raise ValueError("profilePicture must not exceed 5 MB")

        try:
            with Image.open(BytesIO(image_bytes)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError(
                        "profilePicture must be a PNG, JPEG, or WebP image"
                    )
                width, height = image.size
                if width > 4096 or height > 4096:
                    raise ValueError(
                        "profilePicture dimensions must not exceed 4096 by 4096"
                    )
                image.verify()
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError("profilePicture must be a valid image") from error

        return image_bytes


class GoogleWalletSaveResponse(BaseModel):
    save_url: str = Field(serialization_alias="saveUrl")
    object_id: str = Field(serialization_alias="objectId")


class GoogleWalletLinkTooLongError(ValueError):
    pass


app = FastAPI(title="PKPass Builder API", version="1.0.0")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Depends(api_key_header)) -> None:
    expected_api_key = os.getenv("API_KEY")
    if not expected_api_key:
        raise HTTPException(
            status_code=500,
            detail="API_KEY must be configured on the server",
        )
    if api_key is None or not hmac.compare_digest(
        api_key.encode("utf-8"), expected_api_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set when signed mode is enabled")
    return value


def _configured_color(name: str, default: str) -> tuple[int, int, int]:
    value = os.getenv(name, default).strip().removeprefix("#")
    if len(value) != 6 or any(
        character not in "0123456789abcdefABCDEF" for character in value
    ):
        raise ValueError(f"{name} must be a 6-digit hex color (for example #17324d)")
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
    )


def _hex_color(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def _apple_color(color: tuple[int, int, int]) -> str:
    return "rgb({}, {}, {})".format(*color)


def _vcard_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _academic_degree(contact: ContactRequest) -> str | None:
    degree = contact.academic_degree.strip()
    return None if not degree or degree.upper() == "N/A" else degree


def _display_name(contact: ContactRequest) -> str:
    degree = _academic_degree(contact)
    full_name = f"{contact.given_name} {contact.surname}"
    return f"{degree} {full_name}" if degree else full_name


def _show_department() -> bool:
    return os.getenv("SHOW_DEPARTMENT", "").strip().lower() in {"1", "true", "yes", "on"}


def _company_homepage() -> str | None:
    return os.getenv("COMPANY_HOMEPAGE", "").strip() or None


def _address_lines(contact: ContactRequest) -> list[str]:
    return [
        contact.street,
        f"{contact.postal_code} {contact.city}",
        contact.country,
    ]


def _vcard(contact: ContactRequest, company: str) -> str:
    email = str(contact.email)
    degree = _academic_degree(contact)
    org = _vcard_escape(company)
    if _show_department():
        org += f";{_vcard_escape(contact.department)}"
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{_vcard_escape(_display_name(contact))}",
        "N:{};{};;{};".format(
            _vcard_escape(contact.surname),
            _vcard_escape(contact.given_name),
            _vcard_escape(degree or ""),
        ),
        f"TITLE:{_vcard_escape(contact.job_position)}",
        f"ORG:{org}",
        f"EMAIL;TYPE=WORK:{_vcard_escape(email)}",
        f"TEL;TYPE=WORK,VOICE:{_vcard_escape(contact.phone_number)}",
        "ADR;TYPE=WORK:;;{};{};;{};{}".format(
            _vcard_escape(contact.street),
            _vcard_escape(contact.city),
            _vcard_escape(contact.postal_code),
            _vcard_escape(contact.country),
        ),
    ]
    homepage = _company_homepage()
    if homepage:
        lines.append(f"URL:{_vcard_escape(homepage)}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def _google_wallet_localized_string(value: str) -> dict:
    return {"defaultValue": {"language": "en-US", "value": value}}


def build_google_wallet_save_url(contact: ContactRequest) -> tuple[str, str]:
    issuer_id = _required_setting("GOOGLE_WALLET_ISSUER_ID")
    class_id = _required_setting("GOOGLE_WALLET_CLASS_ID")
    if not class_id.startswith(f"{issuer_id}."):
        raise ValueError("GOOGLE_WALLET_CLASS_ID must belong to GOOGLE_WALLET_ISSUER_ID")

    credentials_path = _required_setting("GOOGLE_WALLET_SERVICE_ACCOUNT_JSON")
    try:
        credentials = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(
            "GOOGLE_WALLET_SERVICE_ACCOUNT_JSON must point to a readable credentials file"
        ) from error
    except json.JSONDecodeError as error:
        raise ValueError(
            "Google Wallet service account credentials must be valid JSON"
        ) from error
    except UnicodeError as error:
        raise ValueError(
            "Google Wallet service account credentials must be a UTF-8 JSON file"
        ) from error

    if not isinstance(credentials, dict):
        raise ValueError("Google Wallet service account credentials must be a JSON object")
    service_account_email = credentials.get("client_email")
    private_key = credentials.get("private_key")
    if not service_account_email or not private_key:
        raise ValueError(
            "Google Wallet service account JSON must include client_email and private_key"
        )

    object_id = f"{issuer_id}.{uuid.uuid4()}"
    organization_name = os.getenv("GOOGLE_WALLET_ORGANIZATION_NAME", "Contact Pass")
    vcard = _vcard(contact, organization_name)
    generic_object = {
        "id": object_id,
        "classId": class_id,
        "state": "ACTIVE",
        "cardTitle": _google_wallet_localized_string(organization_name),
        "header": _google_wallet_localized_string(_display_name(contact)),
        "subheader": _google_wallet_localized_string(contact.job_position),
        "hexBackgroundColor": _hex_color(
            _configured_color("GOOGLE_WALLET_BACKGROUND_COLOR", "#17324d")
        ),
        "textModulesData": [
            {
                "header": "Contact details",
                "body": "\n".join(
                    [
                        f"Email: {contact.email}",
                        f"Phone: {contact.phone_number}",
                        *(
                            [f"Department: {contact.department}"]
                            if _show_department()
                            else []
                        ),
                        "Address: " + ", ".join(_address_lines(contact)),
                        *(
                            [f"Website: {_company_homepage()}"]
                            if _company_homepage()
                            else []
                        ),
                    ]
                ),
            }
        ],
        "barcode": {
            "type": "QR_CODE",
            "value": vcard,
            "alternateText": "Scan to add contact",
        },
    }

    claims = {
        "iss": service_account_email,
        "aud": "google",
        "typ": "savetowallet",
        "iat": int(time.time()),
        "payload": {"genericObjects": [generic_object]},
    }
    origins = [
        origin.strip()
        for origin in os.getenv("GOOGLE_WALLET_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if origins:
        claims["origins"] = origins

    try:
        signed_jwt = jwt.encode(claims, private_key, algorithm="RS256")
    except (jwt.PyJWTError, TypeError, ValueError) as error:
        raise RuntimeError(
            "Could not sign Google Wallet JWT; verify the service account private key"
        ) from error

    save_url = f"https://pay.google.com/gp/v/save/{signed_jwt}"
    if len(signed_jwt) > 1800:
        raise GoogleWalletLinkTooLongError(
            "Google Wallet JWT exceeds Google's 1800-character limit; shorten the contact fields"
        )
    return save_url, object_id


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _icon() -> bytes:
    image = Image.new(
        "RGB",
        (58, 58),
        _configured_color("ICON_BACKGROUND_COLOR", "#17324d"),
    )
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    text = "ID"
    bounds = draw.textbbox((0, 0), text, font=font)
    x = (58 - (bounds[2] - bounds[0])) // 2
    y = (58 - (bounds[3] - bounds[1])) // 2 - bounds[1]
    draw.text(
        (x, y),
        text,
        fill=_configured_color("ICON_FOREGROUND_COLOR", "#ffffff"),
        font=font,
    )
    return _png_bytes(image)


def _logo_images() -> dict[str, bytes]:
    logo_path = os.getenv("COMPANY_LOGO_PATH", "").strip()
    if not logo_path:
        return {}

    try:
        with Image.open(logo_path) as source:
            source.load()
            logo = source.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError(
            "COMPANY_LOGO_PATH must point to a readable image file"
        ) from error

    width, height = logo.size
    scale = min(1, 160 / width, 50 / height)
    base_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    images = {}
    for filename, factor in (
        ("logo.png", 1),
        ("logo@2x.png", 2),
        ("logo@3x.png", 3),
    ):
        size = (base_size[0] * factor, base_size[1] * factor)
        images[filename] = _png_bytes(
            logo.resize(size, Image.Resampling.LANCZOS)
        )
    return images


def _profile_picture_images(contact: ContactRequest) -> dict[str, bytes]:
    if contact.profile_picture is None:
        return {}

    with Image.open(BytesIO(contact.profile_picture)) as source:
        source.load()
        profile_picture = ImageOps.fit(
            ImageOps.exif_transpose(source).convert("RGBA"),
            (90, 90),
            method=Image.Resampling.LANCZOS,
        )

    images = {}
    for filename, factor in (
        ("thumbnail.png", 1),
        ("thumbnail@2x.png", 2),
        ("thumbnail@3x.png", 3),
    ):
        size = (90 * factor, 90 * factor)
        images[filename] = _png_bytes(
            profile_picture.resize(size, Image.Resampling.LANCZOS)
        )
    return images


def _pass_json(
    contact: ContactRequest,
    serial_number: str,
    vcard: str,
    pass_type_identifier: str,
    team_identifier: str,
    organization_name: str,
) -> dict:
    address = "\n".join(_address_lines(contact))
    homepage = _company_homepage()
    show_department = _show_department()
    header_fields = [
        {"key": "position", "label": "POSITION", "value": contact.job_position}
    ]
    if show_department:
        header_fields.append(
            {"key": "department", "label": "DEPARTMENT", "value": contact.department}
        )
    secondary_fields = [
        {"key": "street", "label": "STREET", "value": contact.street},
        {
            "key": "city",
            "label": "CITY",
            "value": f"{contact.postal_code} {contact.city}",
        },
    ]
    auxiliary_fields = [
        {"key": "country", "label": "COUNTRY", "value": contact.country}
    ]
    if homepage:
        auxiliary_fields.append(
            {"key": "homepage", "label": "WEBSITE", "value": homepage}
        )
    back_fields = [
        {"key": "email", "label": "Email", "value": str(contact.email)},
        {"key": "phone", "label": "Phone", "value": contact.phone_number},
        {"key": "position", "label": "Job Position", "value": contact.job_position},
    ]
    if show_department:
        back_fields.append(
            {"key": "department", "label": "Department", "value": contact.department}
        )
    back_fields.append({"key": "address", "label": "Address", "value": address})
    if homepage:
        back_fields.append({"key": "homepage", "label": "Website", "value": homepage})
    return {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type_identifier,
        "serialNumber": serial_number,
        "teamIdentifier": team_identifier,
        "organizationName": organization_name,
        "description": "Contact pass",
        "foregroundColor": _apple_color(
            _configured_color("PKPASS_FOREGROUND_COLOR", "#ffffff")
        ),
        "backgroundColor": _apple_color(
            _configured_color("PKPASS_BACKGROUND_COLOR", "#17324d")
        ),
        "labelColor": _apple_color(
            _configured_color("PKPASS_LABEL_COLOR", "#dce6f0")
        ),
        "generic": {
            "primaryFields": [
                {"key": "name", "label": "NAME", "value": _display_name(contact)}
            ],
            "headerFields": header_fields,
            "secondaryFields": secondary_fields,
            "auxiliaryFields": auxiliary_fields,
            "backFields": back_fields,
        },
        "barcodes": [
            {
                "format": "PKBarcodeFormatQR",
                "message": vcard,
                "messageEncoding": "utf-8",
                "altText": "Scan to add contact",
            }
        ],
    }


def _sign_manifest(manifest: bytes) -> bytes:
    certificate = _required_setting("PASS_CERTIFICATE_PATH")
    private_key = _required_setting("PASS_PRIVATE_KEY_PATH")
    wwdr_certificate = _required_setting("APPLE_WWDR_CERTIFICATE_PATH")

    with tempfile.TemporaryDirectory() as temp_dir:
        manifest_path = Path(temp_dir) / "manifest.json"
        signature_path = Path(temp_dir) / "signature"
        manifest_path.write_bytes(manifest)
        try:
            subprocess.run(
                [
                    "openssl",
                    "smime",
                    "-sign",
                    "-binary",
                    "-in",
                    str(manifest_path),
                    "-signer",
                    certificate,
                    "-inkey",
                    private_key,
                    "-certfile",
                    wwdr_certificate,
                    "-out",
                    str(signature_path),
                    "-outform",
                    "DER",
                    "-md",
                    "sha1",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as error:
            raise RuntimeError("OpenSSL is required to sign passes") from error
        except subprocess.CalledProcessError as error:
            detail = error.stderr.strip() or "OpenSSL failed to sign the pass"
            raise RuntimeError(detail) from error
        return signature_path.read_bytes()


def build_pkpass(contact: ContactRequest) -> bytes:
    mode = SigningMode(os.getenv("PKPASS_SIGNING_MODE", "unsigned").lower())
    if mode is SigningMode.signed:
        pass_type_identifier = _required_setting("PASS_TYPE_IDENTIFIER")
        team_identifier = _required_setting("TEAM_IDENTIFIER")
        organization_name = _required_setting("ORGANIZATION_NAME")
    else:
        pass_type_identifier = (
            os.getenv("PASS_TYPE_IDENTIFIER", "").strip()
            or "pass.com.example.contact"
        )
        team_identifier = os.getenv("TEAM_IDENTIFIER", "").strip() or "TEAMID1234"
        organization_name = os.getenv("ORGANIZATION_NAME", "Contact Pass")

    serial_number = str(uuid.uuid4())
    vcard = _vcard(contact, organization_name)

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(vcard)
    qr.make(fit=True)
    qr_image = qr.make_image(
        fill_color=_configured_color("QR_FOREGROUND_COLOR", "#000000"),
        back_color=_configured_color("QR_BACKGROUND_COLOR", "#ffffff"),
    ).convert("RGB")

    icon = _icon()
    icon_image = Image.open(BytesIO(icon))
    files = {
        "pass.json": json.dumps(
            _pass_json(
                contact,
                serial_number,
                vcard,
                pass_type_identifier,
                team_identifier,
                organization_name,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
        "icon.png": _png_bytes(icon_image.resize((29, 29))),
        "icon@2x.png": icon,
        "qr.png": _png_bytes(qr_image),
    }
    files.update(_logo_images())
    files.update(_profile_picture_images(contact))

    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for name, content in files.items():
            package.writestr(name, content)

        if mode is SigningMode.signed:
            manifest = json.dumps(
                {
                    name: hashlib.sha1(content).hexdigest()
                    for name, content in files.items()
                },
                separators=(",", ":"),
            ).encode("utf-8")
            package.writestr("manifest.json", manifest)
            package.writestr("signature", _sign_manifest(manifest))

    return archive.getvalue()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/passes", dependencies=[Depends(verify_api_key)])
def create_pass(contact: ContactRequest) -> Response:
    try:
        contents = build_pkpass(contact)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return Response(
        content=contents,
        media_type="application/vnd.apple.pkpass",
        headers={"Content-Disposition": 'attachment; filename="contact.pkpass"'},
    )


@app.post(
    "/passes/google-wallet",
    response_model=GoogleWalletSaveResponse,
    dependencies=[Depends(verify_api_key)],
)
def create_google_wallet_pass(contact: ContactRequest) -> GoogleWalletSaveResponse:
    try:
        save_url, object_id = build_google_wallet_save_url(contact)
    except GoogleWalletLinkTooLongError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return GoogleWalletSaveResponse(save_url=save_url, object_id=object_id)
