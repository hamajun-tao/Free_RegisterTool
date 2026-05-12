from __future__ import annotations

import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests


CARD_PY_PATH = Path(__file__).resolve().parents[1] / "CTF-pay" / "card.py"


def _load_card_module():
    spec = importlib.util.spec_from_file_location("ctf_pay_card_test", CARD_PY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_create_chatgpt_http_session_falls_back_after_tls_error():
    card = _load_card_module()

    class FakeCurlSession:
        def __init__(self, *args, **kwargs):
            self.headers = {}
            self.proxies = {}
            self.cookies = requests.cookies.RequestsCookieJar()
            self.trust_env = True

        def get(self, url, **kwargs):
            raise RuntimeError(
                "Failed to perform, curl: (35) TLS connect error: "
                "error:00000000:invalid library (0):OPENSSL_internal:invalid library (0)."
            )

    class FakeRequestsSession:
        def __init__(self, *args, **kwargs):
            self.headers = {}
            self.proxies = {}
            self.cookies = requests.cookies.RequestsCookieJar()
            self.trust_env = True
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return SimpleNamespace(
                status_code=200,
                text="{}",
                json=lambda: {},
            )

    with (
        patch.object(card, "_HAS_CURL_CFFI", True),
        patch.object(card, "CurlCffiSession", FakeCurlSession),
        patch.object(card.requests, "Session", FakeRequestsSession),
    ):
        http, transport = card._create_chatgpt_http_session(
            {"proxy": "http://127.0.0.1:7897"},
            user_agent="UnitTestAgent/1.0",
        )

        assert transport == "curl_cffi(chrome136)"
        resp = http.get("https://chatgpt.com/api/auth/session", timeout=3)

        assert resp.status_code == 200


def test_create_chatgpt_http_session_reports_primary_and_fallback_failures():
    card = _load_card_module()

    class FakeCurlSession:
        def __init__(self, *args, **kwargs):
            self.headers = {}
            self.proxies = {}
            self.cookies = requests.cookies.RequestsCookieJar()
            self.trust_env = True

        def get(self, url, **kwargs):
            raise RuntimeError("curl: (35) TLS connect error: curl transport tls boom")

    class FakeRequestsSession:
        def __init__(self, *args, **kwargs):
            self.headers = {}
            self.proxies = {}
            self.cookies = requests.cookies.RequestsCookieJar()
            self.trust_env = True

        def get(self, url, **kwargs):
            raise RuntimeError("requests fallback proxy boom")

    with (
        patch.object(card, "_HAS_CURL_CFFI", True),
        patch.object(card, "CurlCffiSession", FakeCurlSession),
        patch.object(card.requests, "Session", FakeRequestsSession),
    ):
        http, transport = card._create_chatgpt_http_session(
            {"proxy": "http://127.0.0.1:7897"},
            user_agent="UnitTestAgent/1.0",
        )

        assert transport == "curl_cffi(chrome136)"
        with pytest.raises(RuntimeError) as exc_info:
            http.get("https://chatgpt.com/api/auth/session", timeout=3)

    message = str(exc_info.value)
    assert "ChatGPT transport fallback failed" in message
    assert "curl_cffi(chrome136)" in message
    assert "requests(fallback)" in message
    assert "curl transport tls boom" in message
    assert "requests fallback proxy boom" in message


def test_confirm_payment_prefers_refreshed_checkout_amount_over_stale_init_due():
    card = _load_card_module()

    captured = {}

    class FakeSession:
        def post(self, url, data=None, headers=None):
            captured["url"] = url
            captured["data"] = dict(data or {})
            return SimpleNamespace(
                status_code=200,
                text="{}",
                json=lambda: {},
            )

    session = FakeSession()
    init_resp = {
        "total_summary": {"due": 2000},
        "return_url": "https://example.com/return",
    }
    ctx = {
        "confirm_mode": "shared_payment_method",
        "checkout_amount": 0,
        "payment_method_type": "paypal",
    }

    card.confirm_payment(
        session,
        "pk_test_123",
        "cs_test_123",
        "pm_test_123",
        None,
        None,
        init_resp,
        ctx=ctx,
    )

    assert captured["url"].endswith("/v1/payment_pages/cs_test_123/confirm")
    assert captured["data"]["expected_amount"] == "0"


def test_update_payment_page_address_refreshes_checkout_amount_from_latest_response():
    card = _load_card_module()

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = "{}"

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def post(self, url, data=None, headers=None):
            self.calls += 1
            if self.calls == 6:
                return FakeResponse(
                    {
                        "total_summary": {"due": 0, "subtotal": 0, "total": 0},
                        "invoice": {"amount_due": 0, "currency": "usd"},
                    }
                )
            return FakeResponse({})

    session = FakeSession()
    ctx = {}
    card_data = {
        "address": {
            "country": "US",
            "line1": "350 Fifth Avenue",
            "city": "New York",
            "state": "NY",
            "postal_code": "10118",
        }
    }

    with patch.object(card.time, "sleep", lambda *_args, **_kwargs: None):
        card.update_payment_page_address(session, "pk_test_123", "cs_test_123", card_data, ctx)

    assert session.calls == 6
    assert ctx["checkout_amount"] == 0


def test_camoufox_geoip_fallback_retries_without_geoip_when_extra_missing():
    card = _load_card_module()
    attempts = []

    class FakeGeoIPError(RuntimeError):
        pass

    @contextmanager
    def fake_camoufox(**kwargs):
        attempts.append(dict(kwargs))
        if kwargs.get("geoip"):
            raise FakeGeoIPError(
                "Please install the geoip extra to use this feature: pip install camoufox[geoip]"
            )
        yield "fallback-context"

    with (
        patch.object(card, "_camoufox_geoip_extra_available", return_value=True),
        patch.object(card, "_log") as mock_log,
    ):
        with card._camoufox_geoip_fallback(fake_camoufox, headless=True, geoip=True) as ctx:
            assert ctx == "fallback-context"

    assert len(attempts) == 2
    assert attempts[0]["geoip"] is True
    assert attempts[1]["geoip"] is False
    assert any("geoip extra" in str(call.args[0]).lower() for call in mock_log.call_args_list)


def test_paypal_goto_retries_after_ns_error_net_interrupt():
    card = _load_card_module()

    class FakePage:
        def __init__(self):
            self.calls = []
            self.url = "about:blank"

        def goto(self, url, wait_until=None, timeout=None):
            self.calls.append((url, wait_until, timeout))
            if len(self.calls) == 1:
                raise RuntimeError("Page.goto: NS_ERROR_NET_INTERRUPT")
            self.url = "https://www.paypal.com/webapps/hermes"

    page = FakePage()

    with (
        patch.object(card.time, "sleep", lambda *_args, **_kwargs: None),
        patch.object(card.random, "uniform", lambda *_args, **_kwargs: 0),
    ):
        card._paypal_goto_with_retries(
            page,
            "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
            label="[B1-test]",
            attempts=2,
            timeout_ms=1000,
        )

    assert len(page.calls) == 2
    assert page.calls[0][1] == "domcontentloaded"
    assert page.url == "https://www.paypal.com/webapps/hermes"


def test_fetch_auth_session_with_cookie_retries_after_431_using_slim_cookie_header():
    card = _load_card_module()
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            calls.append(
                {
                    "url": url,
                    "cookie": (headers or {}).get("cookie", ""),
                    "timeout": timeout,
                }
            )
            cookie = (headers or {}).get("cookie", "")
            if "tracking_cookie=oversized" in cookie:
                return FakeResponse(431, text="Request Header Fields Too Large")
            return FakeResponse(200, payload={"accessToken": "access-token"})

    data = card._fetch_auth_session_with_cookie(
        FakeSession(),
        cookie_header=(
            "tracking_cookie=oversized;"
            " __Secure-next-auth.session-token=session-token;"
            " cf_clearance=cf-cookie;"
            " oai-did=device-id"
        ),
        user_agent="UnitTestAgent/1.0",
        accept_language="en-US,en;q=0.9",
    )

    assert data["accessToken"] == "access-token"
    assert len(calls) == 2
    assert "tracking_cookie=oversized" in calls[0]["cookie"]
    assert "tracking_cookie=oversized" not in calls[1]["cookie"]
    assert "__Secure-next-auth.session-token=session-token" in calls[1]["cookie"]
    assert "cf_clearance=cf-cookie" in calls[1]["cookie"]
    assert "oai-did=device-id" in calls[1]["cookie"]


def test_filter_essential_cookies_preserves_transient_state_cookies():
    card = _load_card_module()

    lean = card._filter_essential_cookies(
        "tracking_cookie=oversized;"
        " __Secure-next-auth.session-token=session-token;"
        " cf_clearance=cf-cookie;"
        " __cf_bm=bot-cookie;"
        " __cflb=lb-cookie;"
        " _cfuvid=visitor-cookie;"
        " _dd_s=datadome-cookie;"
        " auth_provider=provider-cookie;"
        " oai-did=device-id"
    )

    assert "tracking_cookie=oversized" not in lean
    assert "__Secure-next-auth.session-token=session-token" in lean
    assert "cf_clearance=cf-cookie" in lean
    assert "__cf_bm=bot-cookie" in lean
    assert "__cflb=lb-cookie" in lean
    assert "_cfuvid=visitor-cookie" in lean
    assert "_dd_s=datadome-cookie" in lean
    assert "auth_provider=provider-cookie" in lean
    assert "oai-did=device-id" in lean


def test_fetch_auth_session_with_cookie_logs_slim_retry_failure_after_431():
    card = _load_card_module()
    calls = []

    class FakeResponse:
        def __init__(self, status_code, text=""):
            self.status_code = status_code
            self.text = text

        def json(self):
            return {}

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            calls.append((url, (headers or {}).get("cookie", "")))
            if len(calls) == 1:
                return FakeResponse(431, text="Request Header Fields Too Large")
            return FakeResponse(403, text="missing transient auth state")

    with patch.object(card, "_log") as mock_log:
        with pytest.raises(card.FreshCheckoutAuthError) as exc_info:
            card._fetch_auth_session_with_cookie(
                FakeSession(),
                cookie_header=(
                    "tracking_cookie=oversized;"
                    " __Secure-next-auth.session-token=session-token;"
                    " cf_clearance=cf-cookie;"
                    " auth_provider=provider-cookie"
                ),
                user_agent="UnitTestAgent/1.0",
                accept_language="en-US,en;q=0.9",
            )

    assert len(calls) == 2
    assert "tracking_cookie=oversized" not in calls[1][1]
    assert "403" in str(exc_info.value)
    assert "missing transient auth state" in str(exc_info.value)
    assert any(
        "slim cookie retry status=403" in str(call.args[0])
        for call in mock_log.call_args_list
    )


def test_generate_fresh_checkout_falls_back_to_modern_after_abcard_rejection(monkeypatch):
    card = _load_card_module()
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    class FakeHttp:
        def post(self, url, headers=None, json=None, data=None, timeout=None):
            payload = json or data or {}
            calls.append((url, payload))
            if payload.get("plan_type"):
                return FakeResponse(400, text="Invalid HTTP request received.")
            return FakeResponse(
                200,
                payload={
                    "checkout_session_id": "cs_live_unit",
                    "processor_entity": "openai_llc",
                    "checkout_url": "https://chatgpt.com/checkout/openai_llc/cs_live_unit",
                },
                text="{}",
            )

    cfg = {
        "fresh_checkout": {
            "enabled": True,
            "bootstrap_from_flows": False,
            "request_style": "abcard",
            "fallback_to_modern": True,
            "warmup_chatgpt_context": False,
            "warmup_route_data": False,
            "auth": {
                "mode": "access_token",
                "access_token": "access-token",
                "device_id": "device-id",
            },
            "plan": {
                "plan_name": "chatgptplusplan",
                "billing_country": "US",
                "billing_currency": "USD",
                "promo_campaign_id": "plus-1-month-free",
            },
        }
    }

    fake_http = FakeHttp()
    monkeypatch.setattr(card, "_create_chatgpt_http_session", lambda *args, **kwargs: (fake_http, "fake"))
    monkeypatch.setattr(card, "_log_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "_log_response", lambda *args, **kwargs: None)

    result = card.generate_fresh_checkout(fake_http, cfg, locale_profile=card.LOCALE_PROFILES["US"])

    assert result["checkout_session_id"] == "cs_live_unit"
    assert any(payload.get("plan_type") == "chatgptplusplan" for _, payload in calls)
    assert any(payload.get("plan_name") == "chatgptplusplan" for _, payload in calls)


def test_paypal_goto_retry_exhaustion_reports_attempt_count():
    card = _load_card_module()

    class FakePage:
        def __init__(self):
            self.calls = []
            self.url = "about:blank"

        def goto(self, url, wait_until=None, timeout=None):
            self.calls.append((url, wait_until, timeout))
            raise RuntimeError("Page.goto: NS_ERROR_NET_INTERRUPT")

    page = FakePage()

    with (
        patch.object(card.time, "sleep", lambda *_args, **_kwargs: None),
        patch.object(card.random, "uniform", lambda *_args, **_kwargs: 0),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            card._paypal_goto_with_retries(
                page,
                "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
                label="[B9-test]",
                attempts=3,
                timeout_ms=1000,
            )

    assert len(page.calls) == 3
    message = str(exc_info.value)
    assert "[B9-test]" in message
    assert "failed after 3 attempts" in message
    assert "NS_ERROR_NET_INTERRUPT" in message


def test_paypal_goto_retries_connection_closed_errors():
    card = _load_card_module()

    class FakePage:
        def __init__(self):
            self.calls = []
            self.url = "about:blank"

        def goto(self, url, wait_until=None, timeout=None):
            self.calls.append((url, wait_until, timeout))
            raise RuntimeError(
                "Page.goto: net::ERR_CONNECTION_CLOSED at "
                "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test"
            )

    page = FakePage()

    with (
        patch.object(card.time, "sleep", lambda *_args, **_kwargs: None),
        patch.object(card.random, "uniform", lambda *_args, **_kwargs: 0),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            card._paypal_goto_with_retries(
                page,
                "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
                label="[B1-test]",
                attempts=3,
                timeout_ms=1000,
            )

    assert len(page.calls) == 3
    assert "failed after 3 attempts" in str(exc_info.value)
    assert "ERR_CONNECTION_CLOSED" in str(exc_info.value)


def test_paypal_browser_context_falls_back_from_camoufox_to_playwright(monkeypatch, tmp_path):
    card = _load_card_module()
    camoufox_calls = []
    playwright_launches = []
    fake_page = object()

    class FakeCamoufox:
        def __init__(self, **kwargs):
            camoufox_calls.append(dict(kwargs))

        def __enter__(self):
            raise RuntimeError("camoufox proxy unreachable")

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeScreen:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakePlaywrightContext:
        pages = [fake_page]

        def new_page(self):
            return fake_page

        def close(self):
            pass

    class FakeChromium:
        def launch_persistent_context(self, user_data_dir, **kwargs):
            playwright_launches.append({"user_data_dir": user_data_dir, **kwargs})
            return FakePlaywrightContext()

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            pass

    class FakeSyncPlaywright:
        def start(self):
            return FakePlaywright()

    camoufox_module = types.ModuleType("camoufox")
    camoufox_sync_api = types.ModuleType("camoufox.sync_api")
    camoufox_sync_api.Camoufox = FakeCamoufox
    browserforge_module = types.ModuleType("browserforge")
    fingerprints_module = types.ModuleType("browserforge.fingerprints")
    fingerprints_module.Screen = FakeScreen
    playwright_module = types.ModuleType("playwright")
    playwright_sync_api = types.ModuleType("playwright.sync_api")
    playwright_sync_api.sync_playwright = lambda: FakeSyncPlaywright()

    monkeypatch.setitem(sys.modules, "camoufox", camoufox_module)
    monkeypatch.setitem(sys.modules, "camoufox.sync_api", camoufox_sync_api)
    monkeypatch.setitem(sys.modules, "browserforge", browserforge_module)
    monkeypatch.setitem(sys.modules, "browserforge.fingerprints", fingerprints_module)
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", playwright_sync_api)
    monkeypatch.setattr(card, "_camoufox_geoip_extra_available", lambda: True)

    gen = card._open_paypal_browser_context(
        has_display=True,
        cf_proxy={"server": "http://127.0.0.1:7897"},
        persist_profile=str(tmp_path),
        profile_existed=False,
    )
    try:
        page, ctx = next(gen)
        assert page is fake_page
        assert isinstance(ctx, FakePlaywrightContext)
    finally:
        gen.close()

    assert camoufox_calls
    assert playwright_launches
    assert playwright_launches[0]["headless"] is False
    assert playwright_launches[0]["proxy"] == {"server": "http://127.0.0.1:7897"}
    assert playwright_launches[0]["user_data_dir"] == str(tmp_path / "playwright_chromium")


def test_paypal_browser_context_reports_camoufox_and_playwright_failures(monkeypatch, tmp_path):
    card = _load_card_module()

    class FakeCamoufox:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("camoufox launch boom")

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeScreen:
        def __init__(self, **kwargs):
            pass

    class FakeChromium:
        def launch_persistent_context(self, user_data_dir, **kwargs):
            raise RuntimeError("playwright launch boom")

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            pass

    class FakeSyncPlaywright:
        def start(self):
            return FakePlaywright()

    camoufox_module = types.ModuleType("camoufox")
    camoufox_sync_api = types.ModuleType("camoufox.sync_api")
    camoufox_sync_api.Camoufox = FakeCamoufox
    browserforge_module = types.ModuleType("browserforge")
    fingerprints_module = types.ModuleType("browserforge.fingerprints")
    fingerprints_module.Screen = FakeScreen
    playwright_module = types.ModuleType("playwright")
    playwright_sync_api = types.ModuleType("playwright.sync_api")
    playwright_sync_api.sync_playwright = lambda: FakeSyncPlaywright()

    monkeypatch.setitem(sys.modules, "camoufox", camoufox_module)
    monkeypatch.setitem(sys.modules, "camoufox.sync_api", camoufox_sync_api)
    monkeypatch.setitem(sys.modules, "browserforge", browserforge_module)
    monkeypatch.setitem(sys.modules, "browserforge.fingerprints", fingerprints_module)
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", playwright_sync_api)
    monkeypatch.setattr(card, "_camoufox_geoip_extra_available", lambda: True)

    with pytest.raises(RuntimeError) as exc_info:
        gen = card._open_paypal_browser_context(
            has_display=False,
            cf_proxy=None,
            persist_profile=str(tmp_path),
            profile_existed=False,
        )
        next(gen)

    message = str(exc_info.value)
    assert "PayPal browser fallback failed" in message
    assert "Camoufox" in message
    assert "camoufox launch boom" in message
    assert "Playwright Chromium" in message
    assert "playwright launch boom" in message


def test_paypal_browser_context_uses_camoufox_without_needing_redirect_url(monkeypatch, tmp_path):
    card = _load_card_module()
    camoufox_calls = []
    camoufox_launches = []

    class FakePage:
        def __init__(self):
            self.url = "about:blank"
            self._ctf_pay_browser_engine = ""

        def goto(self, url, wait_until=None, timeout=None):
            camoufox_calls.append((url, wait_until, timeout))
            self.url = url

    fake_page = FakePage()

    class FakeCamoufoxContext:
        pages = [fake_page]

        def new_page(self):
            return fake_page

    class FakeCamoufox:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            camoufox_launches.append(dict(kwargs))

        def __enter__(self):
            return FakeCamoufoxContext()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeScreen:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    camoufox_module = types.ModuleType("camoufox")
    camoufox_sync_api = types.ModuleType("camoufox.sync_api")
    camoufox_sync_api.Camoufox = FakeCamoufox
    browserforge_module = types.ModuleType("browserforge")
    fingerprints_module = types.ModuleType("browserforge.fingerprints")
    fingerprints_module.Screen = FakeScreen

    monkeypatch.setitem(sys.modules, "camoufox", camoufox_module)
    monkeypatch.setitem(sys.modules, "camoufox.sync_api", camoufox_sync_api)
    monkeypatch.setitem(sys.modules, "browserforge", browserforge_module)
    monkeypatch.setitem(sys.modules, "browserforge.fingerprints", fingerprints_module)
    monkeypatch.setattr(card, "_camoufox_geoip_extra_available", lambda: True)

    def _boom_playwright():
        raise AssertionError("Playwright fallback should not be touched when Camoufox succeeds")

    monkeypatch.setattr(card, "_tag_paypal_browser_context", lambda page, ctx, engine: setattr(page, "_ctf_pay_browser_engine", engine))

    playwright_module = types.ModuleType("playwright")
    playwright_sync_api = types.ModuleType("playwright.sync_api")
    playwright_sync_api.sync_playwright = _boom_playwright
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", playwright_sync_api)

    gen = card._open_paypal_browser_context(
        has_display=False,
        cf_proxy={"server": "http://127.0.0.1:7897"},
        persist_profile=str(tmp_path),
        profile_existed=False,
    )
    try:
        page, ctx = next(gen)
    finally:
        gen.close()

    assert page is fake_page
    assert page._ctf_pay_browser_engine == "camoufox"
    assert ctx.pages[0] is fake_page
    assert camoufox_calls == [("https://www.google.com/", "domcontentloaded", 15000)]
    assert camoufox_launches[0]["user_data_dir"] != str(tmp_path)
    assert Path(camoufox_launches[0]["user_data_dir"]).parent == tmp_path / "camoufox_runs"


def test_open_paypal_redirect_context_retries_b1_with_playwright_after_camoufox_failure(
    monkeypatch, tmp_path
):
    card = _load_card_module()
    open_calls = []
    goto_calls = []

    class FakePage:
        def __init__(self, engine):
            self._ctf_pay_browser_engine = engine
            self.url = "about:blank"

    class FakeContext:
        def __init__(self, engine):
            self._ctf_pay_browser_engine = engine

    def fake_open_paypal_browser_context(
        *,
        has_display,
        cf_proxy,
        persist_profile,
        profile_existed,
        locale_profile=None,
    ):
        engine = "camoufox"
        open_calls.append("auto")
        page = FakePage(engine)
        ctx = FakeContext(engine)

        def _gen():
            yield page, ctx

        return _gen()

    @contextmanager
    def fake_open_paypal_playwright_context(
        *,
        has_display,
        cf_proxy,
        persist_profile,
        locale_profile=None,
    ):
        open_calls.append("playwright")
        page = FakePage("playwright")
        ctx = FakeContext("playwright")
        yield page, ctx

    def fake_paypal_goto_with_retries(page, url, label, attempts, timeout_ms, wait_until="domcontentloaded"):
        goto_calls.append(page._ctf_pay_browser_engine)
        if page._ctf_pay_browser_engine == "camoufox":
            raise RuntimeError(f"{label} navigation failed after {attempts} attempts: Page.goto: NS_ERROR_NET_INTERRUPT")
        page.url = "https://www.paypal.com/webapps/hermes"

    monkeypatch.setattr(card, "_open_paypal_browser_context", fake_open_paypal_browser_context)
    monkeypatch.setattr(card, "_open_paypal_playwright_context", fake_open_paypal_playwright_context)
    monkeypatch.setattr(card, "_paypal_goto_with_retries", fake_paypal_goto_with_retries)

    with card._open_paypal_redirect_context(
        redirect_url="https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
        has_display=False,
        cf_proxy={"server": "http://127.0.0.1:7897"},
        persist_profile=str(tmp_path),
        profile_existed=False,
        locale_profile=card.LOCALE_PROFILES["US"],
    ) as (page, ctx):
        assert page.url == "https://www.paypal.com/webapps/hermes"
        assert ctx._ctf_pay_browser_engine == "playwright"

    assert open_calls == ["auto", "playwright"]
    assert goto_calls == ["camoufox", "playwright"]


def test_open_paypal_redirect_context_uses_preresolved_paypal_url(monkeypatch, tmp_path):
    card = _load_card_module()
    goto_urls = []

    class FakePage:
        _ctf_pay_browser_engine = "camoufox"
        url = "about:blank"

    class FakeContext:
        _ctf_pay_browser_engine = "camoufox"

    def fake_open_paypal_browser_context(**kwargs):
        page = FakePage()
        ctx = FakeContext()

        def _gen():
            yield page, ctx

        return _gen()

    def fake_goto(page, url, label, attempts, timeout_ms, wait_until="domcontentloaded"):
        goto_urls.append(url)
        page.url = url

    monkeypatch.setattr(card, "_open_paypal_browser_context", fake_open_paypal_browser_context)
    monkeypatch.setattr(card, "_paypal_goto_with_retries", fake_goto)
    monkeypatch.setattr(
        card,
        "_resolve_paypal_browser_entry_url",
        lambda redirect_url, proxy_url="": "https://www.paypal.com/agreements/approve?ba_token=BA-test",
    )

    with card._open_paypal_redirect_context(
        redirect_url="https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
        has_display=False,
        cf_proxy={"server": "http://127.0.0.1:7897"},
        persist_profile=str(tmp_path),
        profile_existed=False,
        locale_profile=card.LOCALE_PROFILES["US"],
        proxy_url="http://127.0.0.1:7897",
    ) as (page, ctx):
        assert page.url == "https://www.paypal.com/agreements/approve?ba_token=BA-test"

    assert goto_urls == ["https://www.paypal.com/agreements/approve?ba_token=BA-test"]


def test_resolve_paypal_browser_entry_url_follows_stripe_location(monkeypatch):
    card = _load_card_module()
    calls = []

    class FakeResponse:
        def __init__(self, url, location=""):
            self.url = url
            self.headers = {"Location": location} if location else {}

    class FakeSession:
        def __init__(self):
            self.headers = {}
            self.trust_env = True

        def get(self, url, allow_redirects=None, timeout=None):
            calls.append((url, allow_redirects, timeout))
            return FakeResponse(
                url,
                "https://www.paypal.com/agreements/approve?ba_token=BA-test",
            )

    monkeypatch.setattr(card, "_HAS_CURL_CFFI", False)
    monkeypatch.setattr(card.requests, "Session", FakeSession)

    resolved = card._resolve_paypal_browser_entry_url(
        "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
        proxy_url="",
    )

    assert resolved == "https://www.paypal.com/agreements/approve?ba_token=BA-test"
    assert calls == [
        (
            "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
            False,
            15,
        )
    ]


def test_paypal_runtime_billing_defaults_move_non_eu_country_to_eu():
    card = _load_card_module()
    cfg = {
        "paypal": {},
        "fresh_checkout": {
            "plan": {
                "billing_country": "US",
                "billing_currency": "USD",
            }
        },
    }
    card_data = {
        "address": {
            "country": "US",
            "line1": "350 Fifth Avenue",
            "city": "New York",
            "state": "NY",
            "postal_code": "10118",
        }
    }

    changed = card._apply_paypal_runtime_billing_defaults(cfg, card_data)

    assert changed is True
    assert card_data["address"]["country"] in card.EU_COUNTRIES
    assert cfg["fresh_checkout"]["plan"]["billing_country"] == card_data["address"]["country"]
    assert cfg["fresh_checkout"]["plan"]["billing_currency"] == "EUR"


def test_paypal_browser_authorize_does_not_repeat_b1_after_redirect_context(monkeypatch):
    card = _load_card_module()
    goto_calls = []

    class FakePage:
        def __init__(self):
            self.url = "https://chatgpt.com/"
            self.frames = []

        def query_selector(self, selector):
            return None

        def query_selector_all(self, selector):
            return []

        def inner_text(self, selector):
            return ""

        def locator(self, selector):
            raise AssertionError("hCaptcha locator should not run for an already completed redirect")

    class FakeContext:
        def cookies(self):
            return []

    @contextmanager
    def fake_open_paypal_redirect_context(**kwargs):
        yield FakePage(), FakeContext()

    def fake_goto(*args, **kwargs):
        goto_calls.append((args, kwargs))
        raise AssertionError("B1 redirect should be handled exactly once inside _open_paypal_redirect_context")

    monkeypatch.setattr(card, "_open_paypal_redirect_context", fake_open_paypal_redirect_context)
    monkeypatch.setattr(card, "_paypal_goto_with_retries", fake_goto)
    monkeypatch.setattr(card, "_safe_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(card.time, "sleep", lambda *args, **kwargs: None)

    success = card._paypal_browser_authorize(
        "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
        {"email": "payer@example.com", "password": "secret"},
        proxy_url="http://127.0.0.1:7897",
    )

    assert success is True
    assert goto_calls == []


def test_paypal_browser_authorize_uses_ddc_iframe_heuristics_when_slider_handle_missing(
    monkeypatch,
):
    card = _load_card_module()
    drag_points = []

    class FakeFrame:
        url = "https://geo.ddc.paypal.com/captcha/?initialCid=test"

        def __init__(self, page):
            self.page = page

        def inner_text(self, selector):
            return "" if self.page.solved else "Slide the puzzle"

        def query_selector(self, selector):
            return None

    class FakeIframeElement:
        def bounding_box(self):
            return {"x": 80.0, "y": 120.0, "width": 320.0, "height": 180.0}

    class FakeMouse:
        def __init__(self, page):
            self.page = page

        def move(self, x, y):
            drag_points.append(("move", x, y))

        def down(self):
            drag_points.append(("down",))

        def up(self):
            drag_points.append(("up",))
            self.page.solved = True
            self.page.url = "https://chatgpt.com/"

        def click(self, x, y):
            drag_points.append(("click", x, y))

    class FakePage:
        def __init__(self):
            self.url = "https://www.paypal.com/agreements/approve?ba_token=BA-test"
            self.solved = False
            self.mouse = FakeMouse(self)
            self.frames = [FakeFrame(self)]

        def inner_text(self, selector):
            return ""

        def query_selector(self, selector):
            if selector.startswith('iframe['):
                return FakeIframeElement()
            return None

        def query_selector_all(self, selector):
            return []

        def locator(self, selector):
            raise AssertionError("hCaptcha locator should not run in the DDC fallback test")

    class FakeContext:
        def cookies(self):
            return []

    @contextmanager
    def fake_open_paypal_redirect_context(**kwargs):
        yield FakePage(), FakeContext()

    def fake_goto(*args, **kwargs):
        raise AssertionError("redirect context should already own the B1 navigation")

    monkeypatch.setattr(card, "_open_paypal_redirect_context", fake_open_paypal_redirect_context)
    monkeypatch.setattr(card, "_paypal_goto_with_retries", fake_goto)
    monkeypatch.setattr(card, "_safe_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(card.time, "sleep", lambda *args, **kwargs: None)
    monkeypatch.setattr(card.random, "uniform", lambda a, b: (a + b) / 2)
    monkeypatch.setattr(card.random, "randint", lambda a, b: a)

    success = card._paypal_browser_authorize(
        "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
        {"email": "payer@example.com", "password": "secret"},
        proxy_url="http://127.0.0.1:7897",
    )

    assert success is True
    assert drag_points
    move_points = [point for point in drag_points if point[0] == "move"]
    assert move_points
    first_drag = move_points[1]
    last_drag = move_points[-1]
    assert first_drag[1] > 80.0
    assert first_drag[1] < 220.0
    assert first_drag[2] > 200.0
    assert last_drag[1] > 360.0


def test_paypal_browser_authorize_handles_paypal_email_form_on_agreements_page(
    monkeypatch,
):
    card = _load_card_module()
    actions = []

    class FakeElement:
        def __init__(self, page, name, text=""):
            self.page = page
            self.name = name
            self.text = text

        def is_visible(self):
            if self.name == "email":
                return self.page.stage == "email"
            if self.name == "password":
                return self.page.stage == "password"
            return True

        def fill(self, value):
            actions.append((self.name, "fill", value))

        def click(self):
            actions.append((self.name, "click"))
            if self.name == "next":
                self.page.stage = "password"
            elif self.name == "login":
                self.page.url = "https://chatgpt.com/"

        def inner_text(self):
            return self.text

        def get_attribute(self, name):
            return None

    class FakeFrame:
        url = "https://geo.ddc.paypal.com/captcha/?initialCid=test"

        def inner_text(self, selector):
            return ""

        def query_selector(self, selector):
            return None

    class FakePage:
        def __init__(self):
            self.url = "https://www.paypal.com/agreements/approve?ba_token=BA-test"
            self.stage = "email"
            self.frames = [FakeFrame()]

        def inner_text(self, selector):
            return "首先，请输入您的邮箱地址。"

        def query_selector(self, selector):
            if selector in ('input#email', 'input[type="email"]', 'input[autocomplete="username"]'):
                return FakeElement(self, "email")
            if selector in ('#btnNext', 'button:has-text("下一步")', 'button:has-text("Next")'):
                return FakeElement(self, "next", "下一步")
            if selector in ('input#password', 'input[type="password"]:visible', 'input[type="password"]'):
                return FakeElement(self, "password")
            if selector in ('#btnLogin', 'button:has-text("Log In")', 'button[type="submit"]'):
                return FakeElement(self, "login", "Log In")
            return None

        def query_selector_all(self, selector):
            return []

        def wait_for_selector(self, selector, state=None, timeout=None):
            if "password" in selector:
                self.stage = "password"
                return FakeElement(self, "password")
            raise AssertionError(f"unexpected wait_for_selector: {selector}")

        def fill(self, selector, value):
            actions.append((selector, "page.fill", value))

        def locator(self, selector):
            raise AssertionError("hCaptcha locator should not run in the PayPal login test")

    class FakeContext:
        def cookies(self):
            return []

    @contextmanager
    def fake_open_paypal_redirect_context(**kwargs):
        yield FakePage(), FakeContext()

    monkeypatch.setattr(card, "_open_paypal_redirect_context", fake_open_paypal_redirect_context)
    monkeypatch.setattr(card, "_paypal_goto_with_retries", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "_safe_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(card.time, "sleep", lambda *args, **kwargs: None)

    success = card._paypal_browser_authorize(
        "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
        {"email": "payer@example.com", "password": "secret"},
        proxy_url="http://127.0.0.1:7897",
    )

    assert success is True
    assert ("email", "fill", "payer@example.com") in actions
    assert ("next", "click") in actions
    assert ("password", "fill", "secret") in actions
    assert ("login", "click") in actions


def test_paypal_browser_authorize_clicks_login_before_guest_card_email(monkeypatch):
    card = _load_card_module()
    actions = []

    class FakeElement:
        def __init__(self, page, name, text=""):
            self.page = page
            self.name = name
            self.text = text

        def is_visible(self):
            return True

        def fill(self, value):
            actions.append((self.name, "fill", value))

        def click(self):
            actions.append((self.name, "click"))
            if self.name == "login_entry":
                self.page.stage = "login_email"
            elif self.name == "next":
                self.page.stage = "password"
            elif self.name == "login":
                self.page.url = "https://chatgpt.com/"

        def inner_text(self):
            return self.text

        def get_attribute(self, name):
            return None

    class FakePage:
        def __init__(self):
            self.url = "https://www.paypal.com/agreements/approve?ba_token=BA-test"
            self.stage = "guest_card"
            self.frames = []

        def inner_text(self, selector):
            if self.stage == "guest_card":
                return "有PayPal账户？ 登录 使用借记卡 卡号 安全问题"
            return "首先，请输入您的邮箱地址。"

        def query_selector(self, selector):
            if self.stage == "guest_card":
                if selector in ('button:has-text("登录")', 'a:has-text("登录")', 'button:has-text("Log In")'):
                    return FakeElement(self, "login_entry", "登录")
                if selector in ('input[type="email"]', 'input#email', 'input[name="email"]'):
                    return FakeElement(self, "guest_email")
                if selector in ('input[name="card_number"]', 'input[name="cardNumber"]', 'input[autocomplete="cc-number"]'):
                    return FakeElement(self, "card_number")
                return None
            if self.stage == "login_email":
                if selector in ('input#email', 'input[type="email"]', 'input[autocomplete="username"]'):
                    return FakeElement(self, "account_email")
                if selector in ('#btnNext', 'button:has-text("下一步")', 'button:has-text("Next")'):
                    return FakeElement(self, "next", "下一步")
                return None
            if self.stage == "password":
                if selector in ('input#password', 'input[type="password"]:visible', 'input[type="password"]'):
                    return FakeElement(self, "password")
                if selector in ('#btnLogin', 'button:has-text("登录")', 'button:has-text("Log In")', 'button[type="submit"]'):
                    return FakeElement(self, "login", "登录")
            return None

        def query_selector_all(self, selector):
            return []

        def wait_for_selector(self, selector, state=None, timeout=None):
            if "password" in selector:
                self.stage = "password"
                return FakeElement(self, "password")
            if "email" in selector or "username" in selector:
                return FakeElement(self, "account_email")
            raise AssertionError(f"unexpected wait_for_selector: {selector}")

        def locator(self, selector):
            raise AssertionError("hCaptcha locator should not run in the guest-card login test")

    class FakeContext:
        def cookies(self):
            return []

    @contextmanager
    def fake_open_paypal_redirect_context(**kwargs):
        yield FakePage(), FakeContext()

    monkeypatch.setattr(card, "_open_paypal_redirect_context", fake_open_paypal_redirect_context)
    monkeypatch.setattr(card, "_paypal_goto_with_retries", lambda *args, **kwargs: None)
    monkeypatch.setattr(card, "_safe_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(card.time, "sleep", lambda *args, **kwargs: None)

    success = card._paypal_browser_authorize(
        "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test",
        {"email": "payer@example.com", "password": "secret"},
        proxy_url="http://127.0.0.1:7897",
    )

    assert success is True
    assert ("login_entry", "click") in actions
    assert ("guest_email", "fill", "payer@example.com") not in actions
    assert ("account_email", "fill", "payer@example.com") in actions
    assert ("password", "fill", "secret") in actions


def test_paypal_stripe_return_url_detection_accepts_authorize_and_return_urls():
    card = _load_card_module()

    assert card._is_paypal_stripe_return_url(
        "https://pm-redirects.stripe.com/authorize/acct_123/pa_nonce_test"
    ) is True
    assert card._is_paypal_stripe_return_url(
        "https://pm-redirects.stripe.com/return/acct_123/pa_nonce_test"
    ) is True
    assert card._is_paypal_stripe_return_url(
        "https://checkout.stripe.com/c/pay/cs_test?redirect_status=succeeded"
    ) is True
    assert card._is_paypal_stripe_return_url("https://www.paypal.com/webapps/hermes") is False


def test_click_paypal_consent_button_searches_paypal_frames():
    card = _load_card_module()
    clicked = []

    class FakeButton:
        def is_visible(self):
            return True

        def inner_text(self):
            return "同意并继续"

        def scroll_into_view_if_needed(self, timeout=None):
            clicked.append(("scroll", timeout))

        def click(self, timeout=None):
            clicked.append(("click", timeout))

    class FakePage:
        url = "https://www.paypal.com/webapps/hermes?token=EC-test"

        def __init__(self):
            self.frames = [FakeFrame()]

        def query_selector(self, selector):
            return None

    class FakeFrame:
        url = "https://www.paypal.com/webapps/hermes?token=EC-test"

        def query_selector(self, selector):
            if selector == 'button#consentButton':
                return FakeButton()
            return None

    assert card._click_paypal_consent_button(FakePage()) is True
    assert ("click", 5000) in clicked


def test_click_paypal_consent_button_does_not_click_login_next():
    card = _load_card_module()
    clicked = []

    class FakeButton:
        def is_visible(self):
            return True

        def inner_text(self):
            return "下一步"

        def click(self, timeout=None):
            clicked.append(("click", timeout))

    class FakeEmail:
        def is_visible(self):
            return True

    class FakePage:
        url = "https://www.paypal.com/agreements/approve?ba_token=BA-test"
        frames = []

        def query_selector(self, selector):
            if selector in {'input[name="login_email"]', 'input#email', 'input[type="email"]'}:
                return FakeEmail()
            if selector == 'button[type="submit"]':
                return FakeButton()
            return None

    assert card._click_paypal_consent_button(FakePage()) is False
    assert clicked == []


def test_paypal_browser_fast_path_defaults_to_adaptive_mode():
    card = _load_card_module()

    with patch.dict(card.os.environ, {}, clear=True):
        assert card._should_force_paypal_browser_fast_path(
            paypal_cookies_str="x=1",
            paypal_email="payer@example.com",
            paypal_password="secret",
        ) is False
        assert card._should_force_paypal_browser_fast_path(
            paypal_cookies_str="",
            paypal_email="payer@example.com",
            paypal_password="secret",
        ) is True

    with patch.dict(card.os.environ, {"SKIP_HERMES_FAST_PATH": "1"}, clear=True):
        assert card._should_force_paypal_browser_fast_path(
            paypal_cookies_str="x=1",
            paypal_email="payer@example.com",
            paypal_password="secret",
        ) is True


def test_resolve_pre_solve_passive_captcha_defaults_to_false():
    card = _load_card_module()

    assert card._resolve_pre_solve_passive_captcha({}) is False
    assert card._resolve_pre_solve_passive_captcha(
        {"pre_solve_passive_captcha": True}
    ) is True
