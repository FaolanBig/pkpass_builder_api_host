import hmac
import json
import os
from collections.abc import Mapping

from pydantic import ValidationError

from app.main import (
    ContactRequest,
    GoogleWalletLinkTooLongError,
    GoogleWalletSaveResponse,
    build_google_wallet_save_url,
    build_pkpass,
)


def _event_field(event, name, default=None):
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def _response(context, body, status_code, content_type="application/json", headers=None):
    return context.Response(
        body=body,
        headers=headers or {},
        content_type=content_type,
        status_code=status_code,
    )


def _json_response(context, payload, status_code):
    return _response(
        context,
        json.dumps(payload, ensure_ascii=False),
        status_code,
    )


def _validation_errors(error):
    return [
        {
            "loc": list(item["loc"]),
            "msg": item["msg"],
            "type": item["type"],
        }
        for item in error.errors()
    ]


def _contact_from_event(event):
    body = _event_field(event, "body")
    if isinstance(body, Mapping):
        payload = body
    elif isinstance(body, bytes):
        payload = json.loads(body.decode("utf-8"))
    elif isinstance(body, str):
        payload = json.loads(body)
    else:
        raise ValueError("Request body must be a JSON object")

    if not isinstance(payload, Mapping):
        raise ValueError("Request body must be a JSON object")
    return ContactRequest.model_validate(payload)


def _api_key_from_event(event):
    headers = _event_field(event, "headers", {})
    if not isinstance(headers, Mapping):
        return None
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() == "x-api-key" and isinstance(value, str):
            return value
    return None


def handler(context, event):
    """Nuclio HTTP handler for contact-pass and Google Wallet requests."""
    method = _event_field(event, "method", "")
    path = _event_field(event, "path", "")
    if not isinstance(method, str) or not isinstance(path, str):
        return _json_response(context, {"detail": "Invalid HTTP request"}, 400)
    method = method.upper()
    path = path.rstrip("/") or "/"

    if method == "GET" and path == "/health":
        return _json_response(context, {"status": "ok"}, 200)

    if method != "POST" or path not in {"/passes", "/passes/google-wallet"}:
        if path in {"/passes", "/passes/google-wallet", "/health"}:
            return _json_response(
                context,
                {"detail": "Method not allowed"},
                405,
            )
        return _json_response(context, {"detail": "Not found"}, 404)

    expected_api_key = os.getenv("API_KEY")
    if not expected_api_key:
        return _json_response(
            context,
            {"detail": "API_KEY must be configured on the server"},
            500,
        )
    provided_api_key = _api_key_from_event(event)
    if provided_api_key is None or not hmac.compare_digest(
        provided_api_key.encode("utf-8"),
        expected_api_key.encode("utf-8"),
    ):
        return _json_response(
            context,
            {"detail": "Invalid or missing API key"},
            401,
        )

    try:
        contact = _contact_from_event(event)
    except ValidationError as error:
        return _json_response(context, {"detail": _validation_errors(error)}, 422)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        return _json_response(context, {"detail": str(error)}, 400)

    if path == "/passes":
        try:
            package = build_pkpass(contact)
        except (RuntimeError, ValueError) as error:
            return _json_response(context, {"detail": str(error)}, 500)
        return _response(
            context,
            package,
            200,
            content_type="application/vnd.apple.pkpass",
            headers={"Content-Disposition": 'attachment; filename="contact.pkpass"'},
        )

    try:
        save_url, object_id = build_google_wallet_save_url(contact)
    except GoogleWalletLinkTooLongError as error:
        return _json_response(context, {"detail": str(error)}, 422)
    except (RuntimeError, ValueError) as error:
        return _json_response(context, {"detail": str(error)}, 500)

    response = GoogleWalletSaveResponse(save_url=save_url, object_id=object_id)
    return _json_response(context, response.model_dump(by_alias=True), 200)