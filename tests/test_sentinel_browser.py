from platforms.chatgpt.sentinel_browser import (
    _build_account_api_headers,
    _build_account_api_request,
)
import platforms.chatgpt.sentinel_browser as sentinel_browser


def test_build_account_api_request_uses_current_add_phone_endpoints():
    send_request = _build_account_api_request(
        "phone_send",
        "+84947679089",
        "https://auth.openai.com/add-phone",
    )
    validate_request = _build_account_api_request(
        "phone_validate",
        "123456",
        "https://auth.openai.com/add-phone",
    )
    resend_request = _build_account_api_request(
        "phone_otp_resend",
        "{}",
        "https://auth.openai.com/add-phone",
    )

    assert send_request["endpoint"] == "https://auth.openai.com/api/accounts/add-phone/send"
    assert send_request["body"] == '{"phone_number": "+84947679089"}'
    assert validate_request["endpoint"] == "https://auth.openai.com/api/accounts/phone-otp/validate"
    assert validate_request["body"] == '{"code": "123456"}'
    assert resend_request["endpoint"] == "https://auth.openai.com/api/accounts/phone-otp/resend"


def test_create_account_uses_standard_sentinel_header():
    request = _build_account_api_request(
        "create_account",
        '{"name": "Ada Lovelace", "birthdate": "1990-01-01"}',
        "https://auth.openai.com/about-you",
    )

    assert request["endpoint"] == "https://auth.openai.com/api/accounts/create_account"
    assert request["sentinel_header_name"] == "openai-sentinel-token"


def test_build_account_api_headers_includes_device_id_and_sentinel():
    headers = _build_account_api_headers(
        current_url="https://auth.openai.com/about-you",
        sentinel_token='{"token":"sentinel"}',
        sentinel_header_name="openai-sentinel-token",
        device_id="device-fixed",
    )

    assert headers["referer"] == "https://auth.openai.com/about-you"
    assert headers["origin"] == "https://auth.openai.com"
    assert headers["oai-device-id"] == "device-fixed"
    assert headers["openai-sentinel-token"] == '{"token":"sentinel"}'


def test_runtime_import_exposes_browser_fingerprint_generator():
    assert sentinel_browser.BrowserFingerprintGenerator is not None
