import ast
import base64
import json
import unittest
from pathlib import Path
from unittest import mock

from platforms.chatgpt.chatgpt_client import ChatGPTClient, FlowState
from platforms.chatgpt.refresh_token_registration_engine import (
    RefreshTokenRegistrationEngine,
    SignupFormResult,
)
from core.task_runtime import StopTaskRequested


class DummyEmailService:
    service_type = type("ST", (), {"value": "dummy"})()

    def create_email(self):
        return {"email": "user@example.com", "service_id": "svc-1"}

    def get_verification_code(self, **kwargs):
        return "123456"


class SequenceEmailService(DummyEmailService):
    def __init__(self, codes):
        self.codes = list(codes)
        self.calls = []

    def get_verification_code(self, **kwargs):
        self.calls.append(kwargs)
        if not self.codes:
            return None
        return self.codes.pop(0)


class EmptyEmailService(DummyEmailService):
    service_type = type("ST", (), {"value": "custom_provider"})()

    def create_email(self):
        return {"email": "   ", "service_id": "svc-empty"}


class _DummyHTTPClient:
    def __init__(self, sessions):
        self._sessions = list(sessions)
        self._index = 0

    @property
    def session(self):
        return self._sessions[self._index]

    def close(self):
        if self._index < len(self._sessions) - 1:
            self._index += 1


class RegistrationEngineFlowTests(unittest.TestCase):
    @staticmethod
    def _encode_cookie_payload(data):
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _make_engine(self, **kwargs):
        return RefreshTokenRegistrationEngine(
            email_service=DummyEmailService(),
            proxy_url="http://127.0.0.1:7890",
            callback_logger=lambda msg: None,
            **kwargs,
        )

    @staticmethod
    def _add_phone_events(engine):
        events = []
        marker = "add_phone_attempt "
        for line in engine.logs:
            if marker not in line:
                continue
            events.append(json.loads(line.split(marker, 1)[1]))
        return events

    def test_registration_engine_has_no_duplicate_method_definitions(self):
        source_path = Path("platforms/chatgpt/refresh_token_registration_engine.py")
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        engine_class = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "RefreshTokenRegistrationEngine"
        )
        method_names = [
            "_check_sentinel",
            "_register_password",
            "_send_verification_code",
            "_validate_verification_code",
            "_create_user_account",
            "_handle_phone_verification",
        ]

        duplicates = {}
        for name in method_names:
            lines = [
                node.lineno
                for node in engine_class.body
                if isinstance(node, ast.FunctionDef) and node.name == name
            ]
            if len(lines) != 1:
                duplicates[name] = lines

        self.assertEqual(duplicates, {})

    def test_pre_oauth_auto_pay_hook_runs_after_basic_account_before_oauth(self):
        events = []
        engine = self._make_engine(
            pre_oauth_auto_pay_hook=lambda result, runtime: events.append(
                (
                    "hook",
                    result.email,
                    runtime["session_token"],
                    runtime["cookie_header"],
                    runtime["device_id"],
                )
            )
        )
        engine.email = "preoauth@example.com"
        engine.password = "pw"
        engine.session_token = "session-before-oauth"
        engine._device_id = "device-before-oauth"
        engine.session = mock.Mock()
        engine.session.cookies.get_dict.return_value = {
            "__Secure-next-auth.session-token": "session-before-oauth",
            "_puid": "puid-value",
        }

        def create_email():
            events.append(("email",))
            return True

        def create_basic(result):
            events.append(("basic", result.email))
            engine._device_id = "device-before-oauth"
            result.account_id = "acct-preoauth"
            result.session_token = "session-before-oauth"
            return True, ""

        def complete_current(result):
            events.append(("oauth", result.email))
            result.access_token = "access-token"
            result.workspace_id = "workspace-id"
            return True

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")):
            with mock.patch.object(engine, "_create_email", side_effect=create_email):
                with mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", side_effect=create_basic):
                    with mock.patch.object(
                        engine,
                        "_restart_login_flow",
                        return_value=(True, ""),
                    ) as restart_login:
                        with mock.patch.object(
                            engine,
                            "_complete_token_exchange",
                            side_effect=complete_current,
                        ):
                            result = engine.run()

        self.assertTrue(result.success)
        self.assertEqual(events[0], ("email",))
        self.assertEqual(events[1], ("basic", "preoauth@example.com"))
        self.assertEqual(
            events[2],
            (
                "hook",
                "preoauth@example.com",
                "session-before-oauth",
                "__Secure-next-auth.session-token=session-before-oauth; _puid=puid-value",
                "device-before-oauth",
            ),
        )
        self.assertEqual(events[3], ("oauth", "preoauth@example.com"))
        restart_login.assert_called_once()

    def test_pre_oauth_auto_pay_failure_stops_before_oauth(self):
        events = []
        engine = self._make_engine(
            pre_oauth_auto_pay_hook=lambda result, runtime: {
                "plan": "plus",
                "state": "failed_pre_oauth:confirm_failed",
                "flow_order": "before_oauth",
                "error": "Stripe confirm 400",
            }
        )
        engine.email = "preoauth-fail@example.com"
        engine.password = "pw"
        engine.session_token = "session-before-oauth"
        engine.session = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        def create_email():
            events.append(("email",))
            return True

        def create_basic(result):
            events.append(("basic", result.email))
            result.account_id = "acct-preoauth"
            result.session_token = "session-before-oauth"
            return True, ""

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")):
            with mock.patch.object(engine, "_create_email", side_effect=create_email):
                with mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", side_effect=create_basic):
                    with mock.patch.object(
                        engine,
                        "_restart_login_flow",
                        return_value=(True, ""),
                    ) as restart_login:
                        with mock.patch.object(
                            engine,
                            "_complete_token_exchange",
                            return_value=True,
                        ) as complete_current:
                            result = engine.run()

        self.assertFalse(result.success)
        self.assertEqual(events, [("email",), ("basic", "preoauth-fail@example.com")])
        restart_login.assert_not_called()
        complete_current.assert_not_called()
        self.assertEqual(result.metadata["auto_pay"]["state"], "failed_pre_oauth:confirm_failed")
        self.assertIn("pre-oauth payment failed", result.error_message)
        self.assertIn("Stripe confirm 400", result.error_message)

    def test_pre_oauth_auto_pay_hook_exception_stops_before_oauth(self):
        engine = self._make_engine(
            pre_oauth_auto_pay_hook=lambda _result, _runtime: (_ for _ in ()).throw(
                RuntimeError("payment hook exploded")
            )
        )
        engine.email = "preoauth-exception@example.com"
        engine.password = "pw"
        engine.session = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")):
            with mock.patch.object(engine, "_create_email", return_value=True):
                with mock.patch.object(
                    engine,
                    "_create_consumer_chatgpt_basic_account",
                    return_value=(True, ""),
                ):
                    with mock.patch.object(
                        engine,
                        "_restart_login_flow",
                        return_value=(True, ""),
                    ) as restart_login:
                        with mock.patch.object(
                            engine,
                            "_complete_token_exchange",
                            return_value=True,
                        ) as complete_current:
                            result = engine.run()

        self.assertFalse(result.success)
        restart_login.assert_not_called()
        complete_current.assert_not_called()
        self.assertEqual(result.metadata["auto_pay"]["state"], "failed_pre_oauth")
        self.assertIn("payment hook exploded", result.error_message)

    def test_run_surfaces_ip_location_check_reason(self):
        engine = self._make_engine()

        with mock.patch.object(
            engine,
            "_check_ip_location",
            return_value=(False, "proxy trace failed: TLS connect error"),
        ):
            result = engine.run()

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_message,
            "IP location check failed: proxy trace failed: TLS connect error",
        )

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.generate_random_user_info",
        return_value={"name": "Richard Taylor", "birthdate": "1987-03-30"},
    )
    def test_basic_account_success_prepares_chatgpt_session_for_pre_oauth_payment(self, _user_info):
        created_clients = []

        class FakeChatGPTClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self._log = lambda _msg: None
                self.session = mock.Mock()
                self.session.cookies.get.side_effect = lambda name, default=None: (
                    "chatgpt-session-token"
                    if name == "__Secure-next-auth.session-token"
                    else default
                )
                self.device_id = "device-from-chatgpt"
                self.last_registration_state = FlowState(
                    page_type="callback",
                    current_url="https://chatgpt.com/api/auth/callback/openai?code=ok",
                )
                created_clients.append(self)

            def register_complete_flow(self, email, password, first_name, last_name, birthdate, email_adapter):
                self.register_args = (email, password, first_name, last_name, birthdate)
                email_adapter.wait_for_verification_code(email, timeout=300)
                return True, "registered"

        engine = self._make_engine()
        engine.email = "pay-before-oauth@example.com"
        engine.password = "pw"
        result = mock.Mock()
        result.password = ""
        result.access_token = ""
        result.session_token = ""
        result.account_id = ""
        result.workspace_id = ""
        result.refresh_token = ""
        result.id_token = ""
        result.metadata = None

        with mock.patch(
            "platforms.chatgpt.chatgpt_client.ChatGPTClient",
            FakeChatGPTClient,
        ):
            with mock.patch.object(engine, "_complete_post_otp_flow", return_value=True) as complete_post_otp:
                ok, error = engine._create_consumer_chatgpt_basic_account(result)

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(result.access_token, "")
        self.assertEqual(result.refresh_token, "")
        self.assertEqual(result.id_token, "")
        self.assertEqual(result.session_token, "chatgpt-session-token")
        self.assertEqual(engine.session, created_clients[0].session)
        self.assertEqual(engine.session_token, "chatgpt-session-token")
        self.assertEqual(engine._device_id, "device-from-chatgpt")
        self.assertEqual(created_clients[0].kwargs["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(created_clients[0].register_args[0], "pay-before-oauth@example.com")
        complete_post_otp.assert_not_called()
        self.assertEqual(engine._used_verification_codes, {"123456"})

    def test_get_verification_code_excludes_previously_used_codes(self):
        email_service = SequenceEmailService(["111111", "222222"])
        engine = RefreshTokenRegistrationEngine(
            email_service=email_service,
            proxy_url="http://127.0.0.1:7890",
            callback_logger=lambda msg: None,
        )
        engine.email = "user@example.com"
        engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
        engine._otp_sent_at = 100.0

        first_code = engine._get_verification_code()
        second_code = engine._get_verification_code()

        self.assertEqual(first_code, "111111")
        self.assertEqual(second_code, "222222")
        self.assertEqual(email_service.calls[0]["exclude_codes"], set())
        self.assertEqual(email_service.calls[1]["exclude_codes"], {"111111"})
        self.assertEqual(engine._used_verification_codes, {"111111", "222222"})

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.generate_random_user_info",
        return_value={"name": "Ada Lovelace", "birthdate": "1990-01-01"},
    )
    @mock.patch("platforms.chatgpt.chatgpt_client.ChatGPTClient")
    def test_consumer_registration_uses_plain_chatgpt_register_flow(
        self, chatgpt_client_cls, _user_info
    ):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
        engine.password = "Secret123!"

        client = mock.Mock()
        client.session = mock.Mock()
        client.device_id = "device-fixed"
        client.last_registration_state = FlowState(
            page_type="callback",
            current_url="https://chatgpt.com/api/auth/callback/openai?code=ok",
        )
        client.session.cookies.get.side_effect = lambda name, default=None: (
            "session-fixed"
            if name == "__Secure-next-auth.session-token"
            else default
        )
        client.register_complete_flow.return_value = (True, "registered")
        chatgpt_client_cls.return_value = client

        with mock.patch.object(engine, "_submit_browser_form") as submit_browser_form:
            with mock.patch.object(engine, "_complete_post_otp_flow") as complete_post_otp:
                ok, error = engine._create_consumer_chatgpt_basic_account(mock.Mock(password=""))

        self.assertTrue(ok)
        self.assertEqual(error, "")
        client.register_complete_flow.assert_called_once()
        submit_browser_form.assert_not_called()
        complete_post_otp.assert_not_called()
        self.assertIs(engine.session, client.session)
        self.assertEqual(engine._device_id, "device-fixed")

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.generate_random_user_info",
        return_value={"name": "Ada Lovelace", "birthdate": "1990-01-01"},
    )
    @mock.patch("platforms.chatgpt.chatgpt_client.ChatGPTClient")
    def test_consumer_registration_adopts_plain_chatgpt_callback_state(
        self, chatgpt_client_cls, _user_info
    ):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
        engine.password = "Secret123!"

        client = mock.Mock()
        client.session = mock.Mock()
        client.device_id = "device-fixed"
        client.last_registration_state = FlowState(
            page_type="oauth_callback",
            current_url="https://chatgpt.com/api/auth/callback/openai?code=ok",
        )
        client.session.cookies.get.side_effect = lambda name, default=None: default
        client.register_complete_flow.return_value = (True, "registered")
        chatgpt_client_cls.return_value = client

        with mock.patch.object(engine, "_complete_post_otp_flow") as complete_post_otp:
            ok, error = engine._create_consumer_chatgpt_basic_account(mock.Mock(password=""))

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(engine._post_otp_page_type, "oauth_callback")
        self.assertEqual(
            engine._post_otp_continue_url,
            "https://chatgpt.com/api/auth/callback/openai?code=ok",
        )
        complete_post_otp.assert_not_called()

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.generate_random_user_info",
        return_value={"name": "Ada Lovelace", "birthdate": "1990-01-01"},
    )
    @mock.patch("platforms.chatgpt.chatgpt_client.ChatGPTClient")
    def test_consumer_registration_codes_are_excluded_during_later_login(
        self, chatgpt_client_cls, _user_info
    ):
        email_service = SequenceEmailService(["111111"])
        engine = RefreshTokenRegistrationEngine(
            email_service=email_service,
            proxy_url="http://127.0.0.1:7890",
            callback_logger=lambda msg: None,
        )
        engine.email = "user@example.com"
        engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
        engine.password = "Secret123!"

        client = mock.Mock()
        client.session = mock.Mock()
        client.device_id = "device-fixed"
        client.last_registration_state = FlowState(page_type="callback", current_url="https://chatgpt.com/callback")
        client.session.cookies.get.side_effect = lambda name, default=None: (
            "session-fixed"
            if name == "__Secure-next-auth.session-token"
            else default
        )
        def register_complete_flow(_email, _password, _first, _last, _birthdate, email_adapter):
            email_adapter.wait_for_verification_code("user@example.com", timeout=300)
            return True, "registered"
        client.register_complete_flow.side_effect = register_complete_flow
        chatgpt_client_cls.return_value = client

        with mock.patch.object(engine, "_complete_post_otp_flow", return_value=True) as complete_post_otp:
            ok, error = engine._create_consumer_chatgpt_basic_account(mock.Mock(password=""))

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(engine._used_verification_codes, {"111111"})
        client.register_complete_flow.assert_called_once()
        complete_post_otp.assert_not_called()

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.generate_random_user_info",
        return_value={"name": "Ada Lovelace", "birthdate": "1990-01-01"},
    )
    @mock.patch("platforms.chatgpt.chatgpt_client.ChatGPTClient")
    def test_consumer_registration_already_exists_switches_to_login_auth(
        self, chatgpt_client_cls, _user_info
    ):
        engine = self._make_engine()
        engine.email = "used@example.com"
        engine.email_info = {"email": "used@example.com", "service_id": "svc-1"}
        engine.password = "Secret123!"

        client = mock.Mock()
        client.register_complete_flow.return_value = (False, "already_exists")
        chatgpt_client_cls.return_value = client

        with mock.patch.object(engine, "_complete_post_otp_flow") as complete_post_otp:
            ok, error = engine._create_consumer_chatgpt_basic_account(mock.Mock(password=""))

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertTrue(engine._is_existing_account)
        self.assertTrue(engine._token_acquisition_requires_login)
        complete_post_otp.assert_not_called()

    def test_create_email_rejects_blank_email_from_provider(self):
        engine = RefreshTokenRegistrationEngine(
            email_service=EmptyEmailService(),
            proxy_url="http://127.0.0.1:7890",
            callback_logger=lambda msg: None,
        )

        ok = engine._create_email()

        self.assertFalse(ok)
        self.assertIsNone(engine.email)
        self.assertIn("返回空邮箱地址", "\n".join(engine.logs))

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_submit_signup_form_logs_cookie_string_keys_without_crashing(self, mock_browser_form_submit, _sleep):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine._device_id = "device-fixed"
        engine._warmup_page = mock.Mock(return_value=False)

        mock_browser_form_submit.return_value = {
            "status": 200,
            "body": json.dumps({
                "page": {"type": "create_account_password"},
                "continue_url": "https://auth.openai.com/create-account/password",
            }),
            "cookies": [],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/create-account/password",
            "page_type": "create_account_password",
            "continue_url": "https://auth.openai.com/create-account/password",
            "navigation_chain": ["https://auth.openai.com/create-account/password"],
            "storage_state": {},
        }
        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.cookies = mock.Mock()
        session.cookies.get_dict.return_value = {"cf_clearance": "clear-a"}
        engine.session = session

        result = engine._submit_signup_form("device-fixed", "sentinel")

        self.assertTrue(result.success)
        self.assertEqual(result.page_type, "create_account_password")
        mock_browser_form_submit.assert_called_once()

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.warmup_page_and_extract_cookies",
        return_value={"cf_clearance": "clear", "__cf_bm": "bm"},
    )
    def test_warmup_page_accepts_cookie_mapping(self, _warmup):
        engine = self._make_engine()
        session = mock.Mock()
        session.cookies = mock.Mock()
        engine.session = session

        ok = engine._warmup_page("https://auth.openai.com/create-account", "测试预热")

        self.assertTrue(ok)
        session.cookies.set.assert_any_call(
            "cf_clearance", "clear", domain=".openai.com", path="/"
        )
        session.cookies.set.assert_any_call(
            "__cf_bm", "bm", domain=".openai.com", path="/"
        )

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_register_password_classifies_cloudflare_html_block(self, mock_browser_form_submit, _sleep):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine._device_id = "device-fixed"
        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.cookies = mock.Mock()
        session.cookies.get_dict.return_value = {"cf_clearance": "clear-a"}
        engine.session = session
        mock_browser_form_submit.return_value = {
            "status": 403,
            "body": "<!DOCTYPE html><title>Just a moment...</title>",
            "cookies": [],
            "challenge_passed": False,
            "final_url": "https://auth.openai.com/create-account/password",
            "page_type": "create_account_password",
            "continue_url": "",
            "navigation_chain": ["https://auth.openai.com/create-account/password"],
            "storage_state": {},
        }

        ok, password = engine._register_password()

        self.assertFalse(ok)
        self.assertIsNone(password)
        self.assertIn("cloudflare_challenge_blocked", "\n".join(engine.logs))

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_register_password_uses_password_create_sentinel_and_browser_headers(self, mock_browser_form_submit):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine._device_id = "device-fixed"
        engine._generate_password = mock.Mock(return_value="Secret123!")

        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.cookies = mock.Mock()
        session.cookies.get_dict.return_value = {"cf_clearance": "clear-a"}
        engine.session = session
        mock_browser_form_submit.return_value = {
            "status": 200,
            "body": json.dumps({"continue_url": "/email-verification"}),
            "cookies": [],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/email-verification",
            "page_type": "email_otp_verification",
            "continue_url": "https://auth.openai.com/email-verification",
            "navigation_chain": [
                "https://auth.openai.com/create-account/password",
                "https://auth.openai.com/email-verification",
            ],
            "storage_state": {
                "https://auth.openai.com": {
                    "localStorage": {"signup_step": "password_submitted"},
                    "sessionStorage": {"challenge": "passed"},
                }
            },
        }

        ok, password = engine._register_password()

        self.assertTrue(ok)
        self.assertEqual(password, "Secret123!")
        self.assertEqual(engine._register_continue_url, "https://auth.openai.com/email-verification")
        mock_browser_form_submit.assert_called_once()
        self.assertEqual(
            mock_browser_form_submit.call_args.kwargs["initial_storage_state"],
            None,
        )

    def test_sync_browser_form_result_persists_frontend_state_and_post_otp_state(self):
        engine = self._make_engine()
        session = mock.Mock()
        session.cookies = mock.Mock()
        engine.session = session

        browser_result = {
            "status": 200,
            "body": json.dumps(
                {
                    "continue_url": "/sign-in-with-chatgpt/codex/consent",
                    "page": {"type": "consent"},
                }
            ),
            "cookies": [
                {
                    "name": "cf_clearance",
                    "value": "secret-cookie-value",
                    "domain": ".openai.com",
                    "path": "/",
                }
            ],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            "page_type": "consent",
            "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            "navigation_chain": [
                "https://auth.openai.com/add-phone",
                "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            ],
            "storage_state": {
                "https://auth.openai.com": {
                    "localStorage": {"oai/apps/session": "ready"},
                    "sessionStorage": {"oai/session/authorized": "true"},
                }
            },
        }

        response_data = engine._sync_browser_form_result(
            browser_result,
            label="OTP 校验",
            update_post_otp_state=True,
        )

        self.assertEqual(response_data["page"]["type"], "consent")
        self.assertEqual(
            engine._post_otp_continue_url,
            "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        self.assertEqual(engine._post_otp_page_type, "consent")
        self.assertEqual(
            engine._browser_frontend_state["final_url"],
            "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        self.assertEqual(
            engine._browser_frontend_state["storage_state"]["https://auth.openai.com"]["localStorage"]["oai/apps/session"],
            "ready",
        )
        self.assertEqual(
            engine._browser_frontend_state["storage_state"]["https://auth.openai.com"]["sessionStorage"]["oai/session/authorized"],
            "true",
        )
        session.cookies.set.assert_called_once_with(
            "cf_clearance",
            "secret-cookie-value",
            domain=".openai.com",
            path="/",
        )
        self.assertIn("challenge_passed=yes", "\n".join(engine.logs))
        self.assertIn("browser_state_diff", "\n".join(engine.logs))
        self.assertIn("cookies=+[cf_clearance]", "\n".join(engine.logs))
        self.assertIn("oai/apps/session", "\n".join(engine.logs))
        self.assertNotIn("secret-cookie-value", "\n".join(engine.logs))
        self.assertNotIn("ready", "\n".join(engine.logs))

    def test_sync_browser_form_result_logs_state_diff_without_values(self):
        engine = self._make_engine()
        session = mock.Mock()
        session.cookies = mock.Mock()
        engine.session = session
        engine._browser_frontend_state = {
            "challenge_passed": True,
            "cookie_names": ["cf_clearance", "old_cookie"],
            "navigation_chain": ["https://auth.openai.com/add-phone"],
            "storage_state": {
                "https://auth.openai.com": {
                    "localStorage": {"old_key": "old-secret"},
                    "sessionStorage": {"challenge": "passed"},
                }
            },
        }

        browser_result = {
            "status": 200,
            "body": "{}",
            "cookies": [
                {
                    "name": "cf_clearance",
                    "value": "new-clear-secret",
                    "domain": ".openai.com",
                    "path": "/",
                },
                {
                    "name": "oai-client-auth-session",
                    "value": "auth-secret",
                    "domain": ".openai.com",
                    "path": "/",
                },
            ],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            "page_type": "consent",
            "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            "navigation_chain": [
                "https://auth.openai.com/add-phone",
                "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            ],
            "storage_state": {
                "https://auth.openai.com": {
                    "localStorage": {"new_key": "new-secret"},
                    "sessionStorage": {"challenge": "passed"},
                }
            },
        }

        engine._sync_browser_form_result(browser_result, label="状态观测")

        logs = "\n".join(engine.logs)
        self.assertIn("browser_state_diff", logs)
        self.assertIn("oai-client-auth-session", logs)
        self.assertIn("old_cookie", logs)
        self.assertIn("new_key", logs)
        self.assertIn("old_key", logs)
        self.assertNotIn("auth-secret", logs)
        self.assertNotIn("new-secret", logs)
        self.assertNotIn("old-secret", logs)
        self.assertEqual(
            engine._browser_frontend_state["cookie_names"],
            ["cf_clearance", "oai-client-auth-session"],
        )

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_reuses_browser_frontend_state(
        self,
        mock_browser_form_submit,
        mock_smsbower_client_cls,
        mock_config_get,
        mock_getenv,
    ):
        engine = self._make_engine()
        engine._device_id = "device-fixed"
        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.cookies = mock.Mock()
        session.cookies.get_dict.return_value = {
            "cf_clearance": "clear-a",
            "oai-client-auth-session": "auth-cookie",
        }
        engine.session = session

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.return_value = mock.Mock(
            phone_number="84856252657",
            activation_id="act-1",
            quality=None,
        )
        mock_smsbower_client.wait_for_code.return_value = "123456"
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        first_storage_state = {
            "https://auth.openai.com": {
                "localStorage": {"phone_step": "sent"},
                "sessionStorage": {"challenge": "passed"},
            }
        }
        second_storage_state = {
            "https://auth.openai.com": {
                "localStorage": {"phone_step": "validated"},
                "sessionStorage": {"oai/session/authorized": "true"},
            }
        }
        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": "{}",
                "cookies": [
                    {
                        "name": "cf_clearance",
                        "value": "clear-b",
                        "domain": ".openai.com",
                        "path": "/",
                    }
                ],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": first_storage_state,
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [
                    {
                        "name": "oai-client-auth-session",
                        "value": "auth-cookie-2",
                        "domain": ".openai.com",
                        "path": "/",
                    }
                ],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": second_storage_state,
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        self.assertEqual(mock_browser_form_submit.call_count, 2)
        self.assertEqual(
            mock_browser_form_submit.call_args_list[0].kwargs["form_type"],
            "phone_send",
        )
        self.assertEqual(
            mock_browser_form_submit.call_args_list[0].kwargs["form_value"],
            "+84856252657",
        )
        self.assertEqual(
            mock_browser_form_submit.call_args_list[1].kwargs["form_type"],
            "phone_validate",
        )
        self.assertIsNone(
            mock_browser_form_submit.call_args_list[0].kwargs["initial_storage_state"]
        )
        self.assertEqual(
            mock_browser_form_submit.call_args_list[1].kwargs["initial_storage_state"],
            first_storage_state,
        )
        self.assertIn("browser_state_seed cookies=2, storage_keys=0", "\n".join(engine.logs))
        self.assertIn("browser_state_seed cookies=2, storage_keys=2", "\n".join(engine.logs))
        session.post.assert_not_called()
        self.assertEqual(engine._post_otp_page_type, "consent")
        self.assertEqual(
            engine._post_otp_continue_url,
            "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        self.assertEqual(
            engine._browser_frontend_state["storage_state"],
            second_storage_state,
        )
        self.assertLess(
            mock_smsbower_client.mock_calls.index(mock.call.set_status("act-1", 1)),
            mock_smsbower_client.mock_calls.index(
                mock.call.wait_for_code("act-1", timeout=120, interval=5.0, on_poll=mock.ANY)
            ),
        )

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_rotates_number_after_120_second_sms_timeout(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        from core.smsbower import SmsBowerTimeoutError

        first_number = mock.Mock(phone_number="11111111111", activation_id="act-1", quality=None)
        second_number = mock.Mock(phone_number="22222222222", activation_id="act-2", quality=None)
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [first_number, second_number]
        mock_smsbower_client.wait_for_code.side_effect = [
            SmsBowerTimeoutError("timeout"),
            "654321",
        ]
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        self.assertEqual(mock_smsbower_client.get_number.call_count, 2)
        self.assertEqual(
            [call.kwargs["timeout"] for call in mock_smsbower_client.wait_for_code.call_args_list],
            [120, 120],
        )
        self.assertEqual(
            mock_smsbower_client.get_number.call_args_list[1].kwargs["phone_exception"],
            "11111111111",
        )
        mock_smsbower_client.cancel.assert_called_once_with("act-1")
        self.assertIn("Timed out waiting for phone verification code", "\n".join(engine.logs))

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_rotates_number_when_openai_selects_whatsapp_channel(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        first_number = mock.Mock(phone_number="11111111111", activation_id="act-1", quality="gold")
        second_number = mock.Mock(phone_number="22222222222", activation_id="act-2", quality="gold")
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [first_number, second_number]
        mock_smsbower_client.wait_for_code.return_value = "654321"
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "page": {
                            "type": "phone_otp_verification",
                            "payload": {"phone_verification_channel": "whatsapp"},
                        }
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/phone-verification",
                "page_type": "phone_otp_verification",
                "continue_url": "https://auth.openai.com/phone-verification",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "page": {
                            "type": "phone_otp_verification",
                            "payload": {"phone_verification_channel": "sms"},
                        }
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/phone-verification",
                "page_type": "phone_otp_verification",
                "continue_url": "https://auth.openai.com/phone-verification",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        self.assertEqual(mock_smsbower_client.get_number.call_count, 2)
        mock_smsbower_client.wait_for_code.assert_called_once_with(
            "act-2",
            timeout=120,
            interval=5.0,
            on_poll=mock.ANY,
        )
        mock_smsbower_client.cancel.assert_called_once_with("act-1")
        self.assertNotIn(mock.call.set_status("act-1", 1), mock_smsbower_client.mock_calls)
        self.assertIn(mock.call.set_status("act-2", 1), mock_smsbower_client.mock_calls)
        self.assertIn("OpenAI selected whatsapp phone channel", "\n".join(engine.logs))

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_resends_immediately_when_smsbower_requests_retry(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        from core.smsbower import SmsBowerWaitRetryError

        number = mock.Mock(phone_number="11111111111", activation_id="act-1", quality=None)
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.return_value = number
        mock_smsbower_client.wait_for_code.side_effect = [
            SmsBowerWaitRetryError("retry"),
            "654321",
        ]
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "page": {
                            "type": "phone_otp_verification",
                            "payload": {"phone_verification_channel": "sms"},
                        }
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/phone-verification",
                "page_type": "phone_otp_verification",
                "continue_url": "https://auth.openai.com/phone-verification",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        self.assertEqual(mock_smsbower_client.get_number.call_count, 1)
        self.assertEqual(mock_smsbower_client.wait_for_code.call_count, 2)
        self.assertIn(mock.call.set_status("act-1", 3), mock_smsbower_client.mock_calls)
        self.assertIn("requested retry before code arrival", "\n".join(engine.logs))

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_cancels_last_number_after_all_sms_timeouts(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        from core.smsbower import SmsBowerTimeoutError

        numbers = [
            mock.Mock(phone_number="11111111111", activation_id="act-1", quality=None),
            mock.Mock(phone_number="22222222222", activation_id="act-2", quality=None),
            mock.Mock(phone_number="33333333333", activation_id="act-3", quality=None),
        ]
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = numbers
        mock_smsbower_client.wait_for_code.side_effect = [
            SmsBowerTimeoutError("timeout"),
            SmsBowerTimeoutError("timeout"),
            SmsBowerTimeoutError("timeout"),
        ]
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        self.assertEqual(
            [call.args[0] for call in mock_smsbower_client.cancel.call_args_list],
            ["act-1", "act-2", "act-3"],
        )
        self.assertNotIn(
            mock.call("act-3", 6),
            mock_smsbower_client.set_status.call_args_list,
        )

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_retries_get_number_without_phone_exception_when_rejected(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        from core.smsbower import SmsBowerInvalidPhoneExceptionError, SmsBowerTimeoutError

        first_number = mock.Mock(phone_number="84365237020", activation_id="act-1", quality=None)
        second_number = mock.Mock(phone_number="84397008167", activation_id="act-2", quality=None)
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [
            first_number,
            SmsBowerInvalidPhoneExceptionError("WRONG_EXCEPTION_PHONE"),
            second_number,
        ]
        mock_smsbower_client.wait_for_code.side_effect = [
            SmsBowerTimeoutError("timeout"),
            "654321",
        ]
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        self.assertEqual(mock_smsbower_client.get_number.call_count, 3)
        self.assertEqual(
            mock_smsbower_client.get_number.call_args_list[1].kwargs["phone_exception"],
            "84365237020",
        )
        self.assertIsNone(
            mock_smsbower_client.get_number.call_args_list[2].kwargs["phone_exception"]
        )
        self.assertIn("phoneException", "\n".join(engine.logs))

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_tries_next_country_when_smsbower_has_no_numbers(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine(extra_config={"smsbower_country": "10,6"})
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        from core.smsbower import SmsBowerNoNumberError

        second_number = mock.Mock(phone_number="22222222222", activation_id="act-2", quality=None)
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [
            SmsBowerNoNumberError("none in country 10"),
            second_number,
        ]
        mock_smsbower_client.wait_for_code.return_value = "654321"
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        self.assertEqual(
            [call.kwargs["country"] for call in mock_smsbower_client.get_number.call_args_list],
            ["10", "6"],
        )
        self.assertIn("country=10 无可用号码", "\n".join(engine.logs))

    def test_phone_failure_message_reports_no_numbers_instead_of_balance(self):
        engine = self._make_engine()
        engine._post_otp_page_type = "add_phone"
        engine.session = mock.Mock()
        engine.session.get.return_value = mock.Mock(status_code=403, url="https://auth.openai.com/about-you")
        engine._last_phone_failure_reason = "no_numbers"

        result = mock.Mock(error_message="")
        with mock.patch.object(engine, "_get_workspace_id", return_value=""):
            with mock.patch.object(engine, "_handle_phone_verification", return_value=False):
                ok = engine._complete_post_otp_flow(result)

        self.assertFalse(ok)
        self.assertIn("SMSBOWER 当前筛选条件无可用号码", result.error_message)
        self.assertNotIn("确保 SMSBOWER 余额充足", result.error_message)

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    def test_handle_phone_verification_defaults_smsbower_countries_multi(
        self, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        """默认配置使用多国家轮询；当所有国家都无可用号码时，按配置顺序逐个尝试。"""
        engine = self._make_engine()
        engine.session = mock.Mock()

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0

        from core.smsbower import SmsBowerNoNumberError

        mock_smsbower_client.get_number.side_effect = SmsBowerNoNumberError("none")
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        # 默认 13 国列表 "16,10,12,73,33,117,78,86,151,6,22,52,187"，每国调一次
        self.assertEqual(mock_smsbower_client.get_number.call_count, 13)
        first_call = mock_smsbower_client.get_number.call_args_list[0]
        self.assertEqual(first_call.kwargs["country"], "16")
        # 法国仍在列表中
        called_countries = [
            c.kwargs["country"]
            for c in mock_smsbower_client.get_number.call_args_list
        ]
        self.assertIn("78", called_countries)

    def test_parse_smsbower_countries_preserves_configured_order(self):
        self.assertEqual(
            self._make_engine()._parse_smsbower_countries("10,12,22"),
            ["10", "12", "22"],
        )

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    def test_handle_phone_verification_passes_smsbower_price_and_provider_filters(
        self, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()

        config_values = {
            "smsbower_api_key": "demo-key",
            "smsbower_max_price": "0.006",
            "smsbower_provider_ids": "2260,2920",
            "smsbower_except_provider_ids": "2217",
        }
        mock_config_get.side_effect = lambda key, default="": config_values.get(key, default)
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0

        from core.smsbower import SmsBowerNoNumberError

        mock_smsbower_client.get_number.side_effect = SmsBowerNoNumberError("none")
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        kwargs = mock_smsbower_client.get_number.call_args.kwargs
        self.assertEqual(kwargs["max_price"], 0.006)
        self.assertEqual(kwargs["provider_ids"], "2260,2920")
        self.assertEqual(kwargs["except_provider_ids"], "2217")

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_prefers_gold_numbers_with_low_price_steps(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        config_values = {
            "smsbower_api_key": "demo-key",
            "smsbower_country": "10",
            "smsbower_type": "gold",
            "smsbower_max_price": "0.09",
            "smsbower_price_steps": "0.03,0.05,0.09",
        }
        mock_config_get.side_effect = lambda key, default="": config_values.get(key, default)
        mock_getenv.side_effect = lambda key, default=None: default

        from core.smsbower import SmsBowerNoNumberError

        number = mock.Mock(phone_number="84397008167", activation_id="act-2", quality="gold")
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [
            SmsBowerNoNumberError("none under 0.03"),
            number,
        ]
        mock_smsbower_client.wait_for_code.return_value = "654321"
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "page": {
                            "type": "phone_otp_verification",
                            "payload": {"phone_verification_channel": "sms"},
                        }
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/phone-verification",
                "page_type": "phone_otp_verification",
                "continue_url": "https://auth.openai.com/phone-verification",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        self.assertEqual(
            [call.kwargs["max_price"] for call in mock_smsbower_client.get_number.call_args_list],
            [0.03, 0.05],
        )
        self.assertEqual(
            [call.kwargs["quality"] for call in mock_smsbower_client.get_number.call_args_list],
            ["gold", "gold"],
        )

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_tries_all_countries_before_raising_price(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        config_values = {
            "smsbower_api_key": "demo-key",
            "smsbower_country": "52,78",
            "smsbower_type": "gold",
            "smsbower_max_price": "0.019",
            "smsbower_price_steps": "0.006,0.019",
        }
        mock_config_get.side_effect = lambda key, default="": config_values.get(key, default)
        mock_getenv.side_effect = lambda key, default=None: default

        from core.smsbower import SmsBowerNoNumberError

        number = mock.Mock(phone_number="33608105227", activation_id="act-global-cheap", quality="gold")
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [
            SmsBowerNoNumberError("none country 52 under 0.006"),
            SmsBowerNoNumberError("none country 78 under 0.006"),
            number,
        ]
        mock_smsbower_client.wait_for_code.return_value = "654321"
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "page": {
                            "type": "phone_otp_verification",
                            "payload": {"phone_verification_channel": "sms"},
                        }
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/phone-verification",
                "page_type": "phone_otp_verification",
                "continue_url": "https://auth.openai.com/phone-verification",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        self.assertEqual(
            [(call.kwargs["country"], call.kwargs["max_price"]) for call in mock_smsbower_client.get_number.call_args_list],
            [("52", 0.006), ("78", 0.006), ("52", 0.019)],
        )

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_stop_interrupts_sms_wait_and_cancels_activation(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        class StopControl:
            def checkpoint(self, **kwargs):
                raise StopTaskRequested()

        engine = self._make_engine()
        engine._task_control = StopControl()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else default
        )
        mock_getenv.side_effect = lambda key, default=None: default

        number = mock.Mock(phone_number="84397008167", activation_id="act-stop", quality="gold")
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.return_value = number

        def wait_for_code(_activation_id, **kwargs):
            kwargs["on_poll"]("wait", None)
            return "654321"

        mock_smsbower_client.wait_for_code.side_effect = wait_for_code
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.return_value = {
            "status": 200,
            "body": json.dumps(
                {
                    "page": {
                        "type": "phone_otp_verification",
                        "payload": {"phone_verification_channel": "sms"},
                    }
                }
            ),
            "cookies": [],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/phone-verification",
            "page_type": "phone_otp_verification",
            "continue_url": "https://auth.openai.com/phone-verification",
            "navigation_chain": ["https://auth.openai.com/add-phone"],
            "storage_state": {},
        }

        with self.assertRaises(StopTaskRequested):
            engine._handle_phone_verification()

        mock_smsbower_client.cancel.assert_called_once_with("act-stop")

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    def test_handle_phone_verification_uses_extra_config_country_override(
        self, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine(extra_config={"smsbower_country": "22"})
        engine.session = mock.Mock()

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0

        from core.smsbower import SmsBowerNoNumberError

        mock_smsbower_client.get_number.side_effect = SmsBowerNoNumberError("none")
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        mock_smsbower_client.get_number.assert_called_once()
        self.assertEqual(mock_smsbower_client.get_number.call_args.kwargs["country"], "22")

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_stops_retrying_numbers_on_cloudflare_phone_send(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}
        mock_browser_form_submit.return_value = {
            "status": 403,
            "body": "<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>",
            "cookies": [],
            "challenge_passed": False,
            "final_url": "https://auth.openai.com/add-phone",
            "page_type": "add_phone",
            "continue_url": "",
            "navigation_chain": ["https://auth.openai.com/add-phone"],
            "storage_state": {},
        }

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.return_value = mock.Mock(
            phone_number="84856252657",
            activation_id="act-1",
            quality=None,
        )
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        mock_smsbower_client.get_number.assert_called_once()
        self.assertIn("cloudflare_challenge_blocked", "\n".join(engine.logs))

    @mock.patch("time.sleep", return_value=None)
    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_stops_after_three_consecutive_fraud_guard(
        self,
        mock_browser_form_submit,
        mock_smsbower_client_cls,
        mock_config_get,
        mock_getenv,
        _sleep,
    ):
        engine = self._make_engine(extra_config={"fraud_guard_proxy_rotations": "0"})
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        mock_browser_form_submit.return_value = {
            "status": 400,
            "body": json.dumps({"error": {"code": "fraud_guard"}}),
            "cookies": [],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/add-phone",
            "page_type": "add_phone",
            "continue_url": "",
            "navigation_chain": ["https://auth.openai.com/add-phone"],
            "storage_state": {},
        }
        # 需要配置 3 个国家，才能让 fraud_guard 跨国家连续计数达到 3 次后触发 environment_unusable_fraud_guard
        config_values = {
            "smsbower_api_key": "demo-key",
            "smsbower_country": "10,11,12",
            "smsbower_phone_attempts": "10",
        }
        mock_config_get.side_effect = lambda key, default="": config_values.get(key, default)
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [
            mock.Mock(phone_number="841234500001", activation_id="act-1", quality=None),
            mock.Mock(phone_number="849876500002", activation_id="act-2", quality=None),
            mock.Mock(phone_number="842222500003", activation_id="act-3", quality=None),
            mock.Mock(phone_number="843333500004", activation_id="act-4", quality=None),
        ]
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        self.assertEqual(mock_smsbower_client.get_number.call_count, 3)
        mock_smsbower_client.wait_for_code.assert_not_called()
        self.assertEqual(engine._last_phone_failure_reason, "environment_unusable_fraud_guard")
        self.assertIn("environment_unusable", "\n".join(engine.logs))

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_preserves_fraud_guard_reason_after_later_no_numbers(
        self,
        mock_browser_form_submit,
        mock_smsbower_client_cls,
        mock_config_get,
        mock_getenv,
    ):
        engine = self._make_engine(extra_config={"fraud_guard_proxy_rotations": "0"})
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        config_values = {
            "smsbower_api_key": "demo-key",
            "smsbower_country": "6,22",
            "smsbower_phone_attempts": "5",
        }
        mock_config_get.side_effect = lambda key, default="": config_values.get(key, default)
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [
            mock.Mock(phone_number="628111000001", activation_id="act-1", quality=None),
            mock.Mock(phone_number="628222000002", activation_id="act-2", quality=None),
        ]
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 400,
                "body": json.dumps({"error": {"code": "fraud_guard"}}),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 400,
                "body": json.dumps({"error": {"code": "phone_number_in_use"}}),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        self.assertEqual(engine._last_phone_failure_reason, "fraud_guard")

    @mock.patch("time.sleep", return_value=None)
    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_stops_at_account_add_phone_send_limit(
        self,
        mock_browser_form_submit,
        mock_smsbower_client_cls,
        mock_config_get,
        mock_getenv,
        _sleep,
    ):
        engine = self._make_engine(
            extra_config={
                "smsbower_country": "10",
                "add_phone_max_send_attempts": "2",
            }
        )
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        config_values = {
            "smsbower_api_key": "demo-key",
            "smsbower_phone_attempts": "5",
        }
        mock_config_get.side_effect = lambda key, default="": config_values.get(key, default)
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [
            mock.Mock(phone_number="841111000001", activation_id="act-1", quality=None),
            mock.Mock(phone_number="842222000002", activation_id="act-2", quality=None),
            mock.Mock(phone_number="843333000003", activation_id="act-3", quality=None),
        ]
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.return_value = {
            "status": 400,
            "body": json.dumps({"error": {"code": "phone_number_in_use"}}),
            "cookies": [],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/add-phone",
            "page_type": "add_phone",
            "continue_url": "",
            "navigation_chain": ["https://auth.openai.com/add-phone"],
            "storage_state": {},
        }

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        self.assertEqual(mock_browser_form_submit.call_count, 2)
        self.assertEqual(mock_smsbower_client.get_number.call_count, 2)
        self.assertIn(mock.call("act-2"), mock_smsbower_client.cancel.call_args_list)
        self.assertEqual(engine._last_phone_failure_reason, "add_phone_attempt_limit")
        events = self._add_phone_events(engine)
        self.assertEqual([event["phone_prefix"] for event in events], ["841111", "842222"])
        self.assertIn("add_phone_attempt_limit", "\n".join(engine.logs))

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_logs_structured_add_phone_outcomes(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine(extra_config={"smsbower_country": "10"})
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        config_values = {
            "smsbower_api_key": "demo-key",
            "smsbower_provider_ids": "2260",
            "smsbower_phone_attempts": "1",
        }
        mock_config_get.side_effect = lambda key, default="": config_values.get(key, default)
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.return_value = mock.Mock(
            phone_number="841234567890",
            activation_id="act-1",
            quality="gold",
        )
        mock_smsbower_client.wait_for_code.return_value = "654321"
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        events = self._add_phone_events(engine)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["country"], "10")
        self.assertEqual(events[0]["provider"], "2260")
        self.assertEqual(events[0]["phone_prefix"], "841234")
        self.assertEqual(events[0]["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(events[0]["response_code"], 200)
        self.assertEqual(events[0]["error_code"], "")
        self.assertTrue(events[0]["entered_wait_for_code"])

    @mock.patch("time.sleep", return_value=None)
    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_keeps_local_dedupe_after_phone_exception_rejected(
        self,
        mock_browser_form_submit,
        mock_smsbower_client_cls,
        mock_config_get,
        mock_getenv,
        _sleep,
    ):
        engine = self._make_engine(extra_config={"smsbower_country": "10"})
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        config_values = {
            "smsbower_api_key": "demo-key",
            "smsbower_phone_attempts": "4",
        }
        mock_config_get.side_effect = lambda key, default="": config_values.get(key, default)
        mock_getenv.side_effect = lambda key, default=None: default

        from core.smsbower import SmsBowerInvalidPhoneExceptionError, SmsBowerTimeoutError

        first_number = mock.Mock(phone_number="84365237020", activation_id="act-1", quality=None)
        repeated_number = mock.Mock(phone_number="84365237020", activation_id="act-dup", quality=None)
        same_prefix_number = mock.Mock(phone_number="84365237999", activation_id="act-prefix", quality=None)
        good_number = mock.Mock(phone_number="84997008167", activation_id="act-2", quality=None)
        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [
            first_number,
            SmsBowerInvalidPhoneExceptionError("WRONG_EXCEPTION_PHONE"),
            repeated_number,
            same_prefix_number,
            good_number,
        ]
        mock_smsbower_client.wait_for_code.side_effect = [
            SmsBowerTimeoutError("timeout"),
            "654321",
        ]
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        mock_browser_form_submit.side_effect = [
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": "{}",
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/add-phone",
                "page_type": "add_phone",
                "continue_url": "",
                "navigation_chain": ["https://auth.openai.com/add-phone"],
                "storage_state": {},
            },
            {
                "status": 200,
                "body": json.dumps(
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    }
                ),
                "cookies": [],
                "challenge_passed": True,
                "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "page_type": "consent",
                "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "navigation_chain": [
                    "https://auth.openai.com/add-phone",
                    "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                ],
                "storage_state": {},
            },
        ]

        ok = engine._handle_phone_verification()

        self.assertTrue(ok)
        self.assertEqual(mock_smsbower_client.get_number.call_count, 5)
        self.assertEqual(mock_browser_form_submit.call_count, 3)
        self.assertIn("local_dedupe", "\n".join(engine.logs))
        self.assertEqual(
            mock_smsbower_client.get_number.call_args_list[1].kwargs["phone_exception"],
            "84365237020",
        )
        self.assertIsNone(
            mock_smsbower_client.get_number.call_args_list[2].kwargs["phone_exception"]
        )

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_treats_phone_attempts_as_per_country(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}

        mock_browser_form_submit.return_value = {
            "status": 400,
            "body": json.dumps({"error": {"code": "voip_phone_disallowed"}}),
            "cookies": [],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/add-phone",
            "page_type": "add_phone",
            "continue_url": "",
            "navigation_chain": ["https://auth.openai.com/add-phone"],
            "storage_state": {},
        }

        config_values = {
            "smsbower_api_key": "demo-key",
            "smsbower_country": "10,22",
            "smsbower_phone_attempts": "2",
        }
        mock_config_get.side_effect = lambda key, default="": config_values.get(key, default)
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.side_effect = [
            mock.Mock(phone_number="84100000001", activation_id="act-1", quality=None),
            mock.Mock(phone_number="84100000002", activation_id="act-2", quality=None),
            mock.Mock(phone_number="442000000001", activation_id="act-3", quality=None),
            mock.Mock(phone_number="442000000002", activation_id="act-4", quality=None),
        ]
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        self.assertEqual(mock_smsbower_client.get_number.call_count, 4)
        self.assertEqual(
            [call.kwargs["country"] for call in mock_smsbower_client.get_number.call_args_list],
            ["10", "10", "22", "22"],
        )
        self.assertEqual(engine._last_phone_failure_reason, "voip_phone_disallowed")

    @mock.patch("os.getenv")
    @mock.patch("core.config_store.config_store.get")
    @mock.patch("core.smsbower.SmsBowerClient")
    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.browser_form_submit")
    def test_handle_phone_verification_stops_retrying_numbers_on_landline_rejection(
        self, mock_browser_form_submit, mock_smsbower_client_cls, mock_config_get, mock_getenv
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get_dict.return_value = {}
        mock_browser_form_submit.return_value = {
            "status": 400,
            "body": json.dumps({"error": {"code": "landline_disallowed"}}),
            "cookies": [],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/add-phone",
            "page_type": "add_phone",
            "continue_url": "",
            "navigation_chain": ["https://auth.openai.com/add-phone"],
            "storage_state": {},
        }

        mock_config_get.side_effect = (
            lambda key, default="": "demo-key" if key == "smsbower_api_key" else ""
        )
        mock_getenv.side_effect = lambda key, default=None: default

        mock_smsbower_client = mock.Mock()
        mock_smsbower_client.get_balance.return_value = 1.0
        mock_smsbower_client.get_number.return_value = mock.Mock(
            phone_number="84856252657",
            activation_id="act-1",
            quality=None,
        )
        mock_smsbower_client_cls.return_value = mock_smsbower_client

        ok = engine._handle_phone_verification()

        self.assertFalse(ok)
        mock_smsbower_client.get_number.assert_called_once()
        mock_smsbower_client.wait_for_code.assert_not_called()
        self.assertEqual(engine._last_phone_failure_reason, "landline_disallowed")
        self.assertIn("non_retryable_phone_rejection", "\n".join(engine.logs))

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.seed_oai_device_cookie")
    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.generate_device_id",
        return_value="device-fixed",
    )
    def test_get_device_id_reuses_generated_value_across_auth_flow_reset(
        self, _generate_device_id, mock_seed_cookie
    ):
        engine = self._make_engine()
        first_session = mock.Mock()
        first_session.cookies = mock.Mock()
        first_session.get.return_value = mock.Mock(status_code=200)
        second_session = mock.Mock()
        second_session.cookies = mock.Mock()
        second_session.get.return_value = mock.Mock(status_code=200)
        engine.http_client = _DummyHTTPClient([first_session, second_session])

        engine.oauth_start = mock.Mock(auth_url="https://auth.openai.com/oauth/authorize")
        self.assertTrue(engine._init_session())
        first_did = engine._get_device_id()

        engine._reset_auth_flow()
        engine.oauth_start = mock.Mock(auth_url="https://auth.openai.com/oauth/authorize")
        self.assertTrue(engine._init_session())
        second_did = engine._get_device_id()

        self.assertEqual(first_did, "device-fixed")
        self.assertEqual(second_did, "device-fixed")
        _generate_device_id.assert_called_once()
        self.assertEqual(first_session.get.call_count, 1)
        self.assertEqual(second_session.get.call_count, 1)
        self.assertEqual(
            [call.args for call in mock_seed_cookie.call_args_list],
            [
                (first_session, "device-fixed"),
                (second_session, "device-fixed"),
                (second_session, "device-fixed"),
            ],
        )

    def test_run_restarts_login_after_new_registration(self):
        engine = self._make_engine()

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        def create_consumer_basic_account(result):
            engine.password = "pw"
            result.password = "pw"
            return True, ""

        def fake_complete_token_exchange(result):
            result.account_id = "acct-1"
            result.workspace_id = "ws-1"
            result.access_token = "at"
            result.refresh_token = "rt"
            result.id_token = "id"
            result.password = engine.password or "pw"
            return True

        def fake_restart_login_flow():
            engine._token_acquisition_requires_login = True
            return True, ""

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", side_effect=create_consumer_basic_account) as create_basic, \
            mock.patch.object(
                engine,
                "_prepare_authorize_flow",
                side_effect=AssertionError("oauth application entry must not run before consumer ChatGPT account creation"),
            ), \
            mock.patch.object(engine, "_restart_login_flow", side_effect=fake_restart_login_flow) as restart_login, \
            mock.patch.object(engine, "_complete_token_exchange", side_effect=fake_complete_token_exchange) as complete_exchange:
            result = engine.run()

        self.assertTrue(result.success)
        self.assertEqual(result.account_id, "acct-1")
        self.assertEqual(result.refresh_token, "rt")
        self.assertTrue(result.metadata["token_acquired_via_relogin"])
        create_basic.assert_called_once()
        restart_login.assert_called_once()
        complete_exchange.assert_called_once()

    def test_run_requires_real_oauth_credentials_after_restart_login(self):
        engine = self._make_engine()

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        def create_consumer_basic_account(result):
            engine.password = "pw"
            result.password = "pw"
            result.session_token = "web-session"
            result.access_token = "web-access"
            return True, ""

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", side_effect=create_consumer_basic_account), \
            mock.patch.object(engine, "_restart_login_flow", return_value=(True, "")) as restart_login, \
            mock.patch.object(engine, "_complete_token_exchange", return_value=False) as complete_exchange:
            result = engine.run()

        self.assertFalse(result.success)
        self.assertIn("real OAuth credentials", result.error_message)
        self.assertEqual(result.refresh_token, "")
        restart_login.assert_called_once()
        complete_exchange.assert_called_once()

    def test_run_tries_current_consumer_session_before_restart_login(self):
        engine = self._make_engine()
        calls = []

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        def create_consumer_basic_account(result):
            calls.append("create_consumer_basic_account")
            engine.password = "pw"
            result.password = "pw"
            engine.session = mock.Mock()
            return True, ""

        def complete_from_current_session(result):
            calls.append("complete_from_current_session")
            result.account_id = "acct-current"
            result.workspace_id = "ws-current"
            result.access_token = "at-current"
            result.refresh_token = "rt-current"
            result.id_token = "id-current"
            return True

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", side_effect=create_consumer_basic_account), \
            mock.patch.object(
                engine,
                "_complete_token_exchange_from_current_session",
                side_effect=complete_from_current_session,
            ) as current_session_exchange, \
            mock.patch.object(engine, "_restart_login_flow") as restart_login, \
            mock.patch.object(engine, "_complete_token_exchange") as complete_exchange:
            result = engine.run()

        self.assertTrue(result.success)
        self.assertEqual(result.refresh_token, "rt-current")
        self.assertEqual(
            calls,
            [
                "create_consumer_basic_account",
                "complete_from_current_session",
            ],
        )
        current_session_exchange.assert_called_once_with(result)
        restart_login.assert_not_called()
        complete_exchange.assert_not_called()

    def test_run_does_not_restart_login_after_current_session_hits_add_phone_when_session_exists(self):
        engine = self._make_engine()

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        def create_consumer_basic_account(result):
            engine.password = "pw"
            result.password = "pw"
            result.access_token = "web-at"
            result.session_token = "web-session"
            return True, ""

        def current_session_add_phone_blocked(result):
            engine._post_otp_page_type = "add_phone"
            engine._last_phone_failure_reason = "phone_send_failed"
            engine._oauth_blocked_by_phone = True
            result.error_message = "OAuth authorization requires phone verification"
            return False

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", side_effect=create_consumer_basic_account), \
            mock.patch.object(
                engine,
                "_complete_token_exchange_from_current_session",
                side_effect=current_session_add_phone_blocked,
            ), \
            mock.patch.object(engine, "_restart_login_flow") as restart_login, \
            mock.patch.object(engine, "_complete_token_exchange") as complete_exchange:
            result = engine.run()

        self.assertTrue(result.success)
        self.assertEqual(result.access_token, "web-at")
        self.assertEqual(result.session_token, "web-session")
        self.assertEqual(result.refresh_token, "")
        self.assertEqual(result.metadata["oauth_state"], "blocked_by_phone")
        restart_login.assert_not_called()
        complete_exchange.assert_not_called()

    def test_run_does_not_restart_login_after_current_session_fraud_guard_without_proxy_rotation(self):
        engine = self._make_engine(extra_config={"fraud_guard_proxy_rotations": "0"})

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        def create_consumer_basic_account(result):
            engine.password = "pw"
            result.password = "pw"
            result.access_token = "web-at"
            result.session_token = "web-session"
            return True, ""

        def current_session_fraud_guard(result):
            engine._post_otp_page_type = "add_phone"
            engine._last_phone_failure_reason = "fraud_guard"
            result.error_message = "OpenAI requires phone verification after fraud_guard"
            return False

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", side_effect=create_consumer_basic_account), \
            mock.patch.object(
                engine,
                "_complete_token_exchange_from_current_session",
                side_effect=current_session_fraud_guard,
            ), \
            mock.patch.object(engine, "_restart_login_flow") as restart_login, \
            mock.patch.object(engine, "_complete_token_exchange") as complete_exchange:
            result = engine.run()

        self.assertTrue(result.success)
        self.assertEqual(result.access_token, "web-at")
        self.assertEqual(result.session_token, "web-session")
        self.assertEqual(result.refresh_token, "")
        self.assertEqual(result.metadata["oauth_state"], "blocked_by_phone")
        restart_login.assert_not_called()
        complete_exchange.assert_not_called()

    def test_current_session_token_exchange_does_not_force_oauth_login_prompt(self):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies.get.return_value = None

        def resolve_callback(start_url):
            self.assertNotIn("prompt=login", start_url)
            return (
                "http://localhost:1455/auth/callback?code=code-1&state="
                + engine.oauth_start.state,
                "ws-current",
            )

        with mock.patch.object(engine, "_resolve_oauth_callback_url", side_effect=resolve_callback), \
            mock.patch.object(
                engine,
                "_handle_oauth_callback",
                return_value={
                    "access_token": "at-current",
                    "refresh_token": "rt-current",
                    "id_token": "id-current",
                },
            ):
            result = mock.Mock(workspace_id="")
            ok = engine._complete_token_exchange_from_current_session(result)

        self.assertTrue(ok)
        self.assertEqual(result.refresh_token, "rt-current")
        self.assertNotIn("prompt=login", engine.oauth_start.auth_url)

    def test_run_prepares_basic_account_before_token_authorization_for_new_registration(self):
        engine = self._make_engine()
        calls = []

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        def create_consumer_basic_account(result):
            calls.append("create_consumer_basic_account")
            engine.password = "pw"
            result.password = "pw"
            return True, ""

        def restart_login_flow():
            calls.append("restart_login_flow")
            engine._token_acquisition_requires_login = True
            return True, ""

        def complete_token_exchange(result):
            calls.append("complete_token_exchange")
            result.account_id = "acct-1"
            result.workspace_id = "ws-1"
            result.access_token = "at"
            result.refresh_token = "rt"
            result.id_token = "id"
            return True

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", side_effect=create_consumer_basic_account) as create_basic, \
            mock.patch.object(
                engine,
                "_prepare_authorize_flow",
                side_effect=AssertionError("oauth application entry must not run before consumer ChatGPT account creation"),
            ), \
            mock.patch.object(engine, "_restart_login_flow", side_effect=restart_login_flow), \
            mock.patch.object(engine, "_complete_token_exchange", side_effect=complete_token_exchange):
            result = engine.run()

        self.assertTrue(result.success)
        create_basic.assert_called_once()
        self.assertEqual(
            calls,
            [
                "create_consumer_basic_account",
                "restart_login_flow",
                "complete_token_exchange",
            ],
        )

    def test_prepare_basic_account_flow_uses_oauth_registration_entry_label(self):
        engine = self._make_engine()

        with mock.patch.object(
            engine,
            "_prepare_authorize_flow",
            return_value=("did", "sentinel"),
        ) as prepare_authorize:
            result = engine._prepare_basic_account_flow()

        self.assertEqual(result, ("did", "sentinel"))
        prepare_authorize.assert_called_once_with("OAuth 注册入口会话")

    def test_run_delegates_basic_account_edge_states_to_consumer_registration_before_token_authorization(self):
        engine = self._make_engine()
        calls = []

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        def create_consumer_basic_account(result):
            calls.append("create_consumer_basic_account")
            result.password = "pw"
            return True, ""

        def restart_login_flow():
            calls.append("restart_login_flow")
            engine._token_acquisition_requires_login = True
            return True, ""

        def complete_token_exchange(result):
            calls.append("complete_token_exchange")
            result.account_id = "acct-1"
            result.workspace_id = "ws-token"
            result.access_token = "at"
            result.refresh_token = "rt"
            result.id_token = "id"
            return True

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", side_effect=create_consumer_basic_account) as create_basic, \
            mock.patch.object(engine, "_prepare_basic_account_flow") as prepare_basic, \
            mock.patch.object(engine, "_submit_signup_form") as submit_signup, \
            mock.patch.object(engine, "_register_password") as register_password, \
            mock.patch.object(engine, "_send_verification_code") as send_otp, \
            mock.patch.object(engine, "_get_verification_code") as get_otp, \
            mock.patch.object(engine, "_validate_verification_code") as validate_otp, \
            mock.patch.object(engine, "_complete_post_otp_flow") as complete_basic, \
            mock.patch.object(engine, "_ensure_basic_account_ready_for_token_authorization") as ensure_basic, \
            mock.patch.object(engine, "_restart_login_flow", side_effect=restart_login_flow), \
            mock.patch.object(engine, "_complete_token_exchange", side_effect=complete_token_exchange):
            result = engine.run()

        self.assertTrue(result.success)
        self.assertEqual(
            calls,
            [
                "create_consumer_basic_account",
                "restart_login_flow",
                "complete_token_exchange",
            ],
        )
        create_basic.assert_called_once()
        prepare_basic.assert_not_called()
        submit_signup.assert_not_called()
        register_password.assert_not_called()
        send_otp.assert_not_called()
        get_otp.assert_not_called()
        validate_otp.assert_not_called()
        complete_basic.assert_not_called()
        ensure_basic.assert_not_called()

    def test_run_stops_before_token_authorization_when_consumer_basic_account_creation_fails(self):
        engine = self._make_engine()

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(
                engine,
                "_create_consumer_chatgpt_basic_account",
                return_value=(False, "consumer_chatgpt_registration_failed: add_phone_blocked"),
            ) as create_basic, \
            mock.patch.object(engine, "_restart_login_flow") as restart_login, \
            mock.patch.object(engine, "_complete_token_exchange") as complete_exchange:
            result = engine.run()

        self.assertFalse(result.success)
        self.assertIn("consumer_chatgpt_registration_failed", result.error_message)
        create_basic.assert_called_once()
        restart_login.assert_not_called()
        complete_exchange.assert_not_called()

    def test_run_does_not_use_oauth_signup_shortcut_before_consumer_registration(self):
        engine = self._make_engine()

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        def fake_complete_token_exchange(result):
            result.account_id = "acct-existing"
            result.workspace_id = "ws-existing"
            result.access_token = "at"
            result.refresh_token = "rt"
            result.id_token = "id"
            result.source = "login"
            return True

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(engine, "_create_consumer_chatgpt_basic_account", return_value=(True, "")) as create_basic, \
            mock.patch.object(engine, "_prepare_authorize_flow") as prepare_authorize, \
            mock.patch.object(engine, "_submit_signup_form") as submit_signup, \
            mock.patch.object(engine, "_register_password") as register_password, \
            mock.patch.object(engine, "_send_verification_code") as send_otp, \
            mock.patch.object(engine, "_get_verification_code") as get_otp, \
            mock.patch.object(engine, "_validate_verification_code") as validate_otp, \
            mock.patch.object(engine, "_create_user_account") as create_account, \
            mock.patch.object(engine, "_restart_login_flow", return_value=(True, "")) as restart_login, \
            mock.patch.object(engine, "_complete_token_exchange", side_effect=fake_complete_token_exchange) as complete_exchange:
            result = engine.run()

        self.assertTrue(result.success)
        self.assertEqual(result.source, "login")
        create_basic.assert_called_once()
        prepare_authorize.assert_not_called()
        submit_signup.assert_not_called()
        register_password.assert_not_called()
        send_otp.assert_not_called()
        get_otp.assert_not_called()
        validate_otp.assert_not_called()
        create_account.assert_not_called()
        restart_login.assert_called_once()
        complete_exchange.assert_called_once()

    def test_run_fails_cleanly_when_consumer_registration_reports_login_password_page(self):
        engine = self._make_engine()

        def fake_create_email():
            engine.email_info = {"email": "user@example.com", "service_id": "svc-1"}
            engine.email = "user@example.com"
            return True

        with mock.patch.object(engine, "_check_ip_location", return_value=(True, "US")), \
            mock.patch.object(engine, "_create_email", side_effect=fake_create_email), \
            mock.patch.object(
                engine,
                "_create_consumer_chatgpt_basic_account",
                return_value=(False, "consumer_chatgpt_registration_failed: login_password_page"),
            ) as create_basic, \
            mock.patch.object(engine, "_prepare_authorize_flow") as prepare_authorize, \
            mock.patch.object(engine, "_submit_signup_form") as submit_signup, \
            mock.patch.object(engine, "_register_password") as register_password, \
            mock.patch.object(engine, "_generate_password") as generate_password, \
            mock.patch.object(engine, "_submit_login_password") as submit_login_password, \
            mock.patch.object(engine, "_send_verification_code") as send_otp, \
            mock.patch.object(engine, "_get_verification_code") as get_otp, \
            mock.patch.object(engine, "_validate_verification_code") as validate_otp, \
            mock.patch.object(engine, "_create_user_account") as create_account, \
            mock.patch.object(engine, "_restart_login_flow") as restart_login, \
            mock.patch.object(engine, "_mark_email_as_registered") as mark_registered, \
            mock.patch.object(engine, "_complete_token_exchange") as complete_exchange:
            result = engine.run()

        self.assertFalse(result.success)
        self.assertIn("consumer_chatgpt_registration_failed", result.error_message)
        self.assertIn("login_password_page", result.error_message)
        create_basic.assert_called_once()
        prepare_authorize.assert_not_called()
        submit_signup.assert_not_called()
        mark_registered.assert_not_called()
        register_password.assert_not_called()
        generate_password.assert_not_called()
        submit_login_password.assert_not_called()
        send_otp.assert_not_called()
        get_otp.assert_not_called()
        validate_otp.assert_not_called()
        create_account.assert_not_called()
        restart_login.assert_not_called()
        complete_exchange.assert_not_called()

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_post_otp_add_phone_does_not_treat_consent_403_as_success(self, _sleep):
        engine = self._make_engine()
        engine._post_otp_page_type = "add_phone"

        consent_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        about_response = mock.Mock(
            status_code=200,
            url="https://auth.openai.com/add-phone",
        )
        session = mock.Mock()
        session.get.side_effect = [consent_response, about_response]
        session.cookies.get.return_value = None
        engine.session = session

        result = SignupFormResult(success=False)
        with mock.patch.object(engine, "_get_workspace_id", return_value=None) as get_workspace:
            with mock.patch.object(engine, "_handle_phone_verification", return_value=False) as phone_verify:
                ok = engine._complete_post_otp_flow(result)

        self.assertFalse(ok)
        self.assertEqual(engine._post_otp_page_type, "add_phone")
        self.assertIn("要求绑定手机号", result.error_message)
        get_workspace.assert_called_once()
        phone_verify.assert_called_once()
        self.assertIn("consent 页面返回 403", "\n".join(engine.logs))

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_post_otp_add_phone_does_not_treat_about_you_403_as_success(self, _sleep):
        engine = self._make_engine()
        engine._post_otp_page_type = "add_phone"

        consent_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        about_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/about-you",
        )
        session = mock.Mock()
        session.get.side_effect = [consent_response, about_response]
        session.cookies.get.return_value = None
        engine.session = session

        result = SignupFormResult(success=False)
        with mock.patch.object(engine, "_get_workspace_id", return_value=None):
            with mock.patch.object(engine, "_handle_phone_verification", return_value=False):
                ok = engine._complete_post_otp_flow(result)

        logs = "\n".join(engine.logs)
        self.assertFalse(ok)
        self.assertEqual(engine._post_otp_page_type, "add_phone")
        self.assertNotIn("可能成功绕过", logs)
        self.assertIn("about-you 页面返回 403", logs)

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_post_otp_add_phone_logs_that_about_you_profile_is_not_created_yet(self, _sleep):
        engine = self._make_engine()
        engine._post_otp_page_type = "add_phone"

        consent_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        about_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/about-you",
        )
        session = mock.Mock()
        session.get.side_effect = [consent_response, about_response]
        session.cookies = mock.Mock()
        session.cookies.get.return_value = None
        engine.session = session

        result = SignupFormResult(success=False)
        with mock.patch.object(engine, "_get_workspace_id", return_value=None):
            with mock.patch.object(engine, "_handle_phone_verification", return_value=False):
                ok = engine._complete_post_otp_flow(result, exchange_token=False)

        logs = "\n".join(engine.logs)
        self.assertFalse(ok)
        self.assertIn("尚未进入 about-you", logs)
        self.assertIn("姓名/生日资料尚未创建", logs)

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_complete_token_exchange_reports_login_auth_stage_when_add_phone_blocks(self, _sleep):
        engine = self._make_engine()
        engine._token_acquisition_requires_login = True
        engine._post_otp_page_type = "add_phone"
        engine._post_otp_continue_url = "https://auth.openai.com/add-phone"

        consent_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        about_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/about-you",
        )
        session = mock.Mock()
        session.get.side_effect = [consent_response, about_response]
        session.cookies = mock.Mock()
        session.cookies.get.return_value = None
        engine.session = session

        result = SignupFormResult(success=False)

        with mock.patch.object(engine, "_get_verification_code", return_value="123456"):
            with mock.patch.object(engine, "_validate_verification_code", return_value=True):
                with mock.patch.object(engine, "_get_workspace_id", return_value=None):
                    with mock.patch.object(engine, "_handle_phone_verification", return_value=False):
                        ok = engine._complete_token_exchange(result)

        self.assertFalse(ok)
        self.assertIn("登录授权失败", result.error_message)
        self.assertIn("要求绑定手机号", result.error_message)

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_complete_token_exchange_handles_add_phone_during_login_authorization(self, _sleep):
        engine = self._make_engine()
        engine._token_acquisition_requires_login = True
        engine.email = "user@example.com"
        engine._post_otp_page_type = "add_phone"
        engine._post_otp_continue_url = "https://auth.openai.com/add-phone"

        auth_cookie = self._encode_cookie_payload(
            {
                "email": "user@example.com",
                "email_verification_mode": "onboard",
                "workspaces": [],
            }
        )
        consent_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        about_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/about-you",
        )
        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.get.side_effect = [consent_response, about_response]
        session.cookies = mock.Mock()
        session.cookies.get.return_value = None
        session.cookies.get_dict.return_value = {"oai-client-auth-session": auth_cookie}
        session.cookies.get.side_effect = lambda name, default=None: (
            auth_cookie if name == "oai-client-auth-session" else default
        )
        engine.session = session

        result = SignupFormResult(success=False)

        def validate_code(_code):
            engine._post_otp_page_type = "add_phone"
            engine._post_otp_continue_url = "https://auth.openai.com/add-phone"
            return True

        def phone_success():
            engine._post_otp_page_type = "consent"
            engine._post_otp_continue_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
            return True

        with mock.patch.object(engine, "_get_verification_code", return_value="123456"):
            with mock.patch.object(engine, "_validate_verification_code", side_effect=validate_code):
                with mock.patch.object(engine, "_get_workspace_id", side_effect=[None, "ws-auth"]) as get_workspace:
                    with mock.patch.object(engine, "_handle_phone_verification", side_effect=phone_success) as phone_verify:
                        with mock.patch.object(engine, "_resolve_oauth_callback_via_browser", return_value="") as browser_callback:
                            with mock.patch.object(
                                engine,
                                "_select_workspace",
                                return_value="http://localhost:1455/auth/callback?code=auth-code&state=oauth-state",
                            ) as select_workspace:
                                with mock.patch.object(
                                    engine,
                                    "_follow_redirects",
                                    return_value="http://localhost:1455/auth/callback?code=auth-code&state=oauth-state",
                                ):
                                    with mock.patch.object(
                                        engine,
                                        "_handle_oauth_callback",
                                        return_value={
                                            "account_id": "acct-1",
                                            "access_token": "at",
                                            "refresh_token": "rt",
                                            "id_token": "id",
                                        },
                                    ):
                                        ok = engine._complete_token_exchange(result)

        self.assertTrue(ok)
        self.assertEqual(result.workspace_id, "ws-auth")
        self.assertEqual(result.refresh_token, "rt")
        self.assertEqual(result.source, "register")
        self.assertEqual(get_workspace.call_count, 2)
        phone_verify.assert_called_once()
        browser_callback.assert_called_once_with(
            "ws-auth",
            "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        select_workspace.assert_called_once_with("ws-auth")

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.build_sentinel_token",
        return_value='{"flow":"password_verify"}',
    )
    def test_submit_login_password_uses_password_verify_sentinel(self, mock_build_sentinel):
        engine = self._make_engine()
        engine._device_id = "device-fixed"
        engine.password = "Secret123!"
        response = mock.Mock(
            status_code=200,
            text=json.dumps({
                "page": {"type": "email_otp_verification"},
                "continue_url": "/email-verification",
            }),
        )
        engine.session = mock.Mock()
        engine.session.post.return_value = response

        with mock.patch.object(
            engine,
            "_build_json_headers",
            return_value={"x-test-header": "1"},
        ) as build_headers:
            result = engine._submit_login_password()

        self.assertTrue(result.success)
        mock_build_sentinel.assert_called_once_with(
            engine.session,
            "device-fixed",
            flow="password_verify",
        )
        build_headers.assert_called_once_with(
            referer="https://auth.openai.com/log-in/password",
            include_device_id=True,
            include_datadog=True,
        )
        engine.session.post.assert_called_once_with(
            "https://auth.openai.com/api/accounts/password/verify",
            headers={
                "x-test-header": "1",
                "openai-sentinel-token": '{"flow":"password_verify"}',
            },
            json={"password": "Secret123!"},
            timeout=30,
        )

    def test_log_callback_unicode_encode_error_does_not_escape(self):
        def failing_callback(_msg):
            raise UnicodeEncodeError("gbk", "✅", 0, 1, "illegal multibyte sequence")

        engine = RefreshTokenRegistrationEngine(
            email_service=DummyEmailService(),
            proxy_url="http://127.0.0.1:7890",
            callback_logger=failing_callback,
        )

        engine._log("✅ 注册流程完成")

        self.assertIn("✅ 注册流程完成", "\n".join(engine.logs))

    def test_restart_login_flow_accepts_direct_email_otp_page(self):
        engine = self._make_engine()

        with mock.patch.object(engine, "_reset_auth_flow") as reset_auth_flow, \
            mock.patch.object(engine, "_prepare_authorize_flow", return_value=("did-1", "sentinel-1")) as prepare_flow, \
            mock.patch.object(
                engine,
                "_submit_login_start",
                return_value=SignupFormResult(
                    success=True,
                    page_type="email_otp_verification",
                    is_existing_account=True,
                ),
            ) as submit_login_start, \
            mock.patch.object(engine, "_submit_login_password") as submit_login_password:
            ok, error = engine._restart_login_flow()

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertTrue(engine._token_acquisition_requires_login)
        reset_auth_flow.assert_called_once()
        prepare_flow.assert_called_once_with("重新登录")
        submit_login_start.assert_called_once_with("did-1", "sentinel-1")
        submit_login_password.assert_not_called()

    def test_resolve_oauth_callback_url_handles_organization_select_redirect(self):
        engine = self._make_engine()
        engine._device_id = "device-fixed"
        engine.session = mock.Mock()
        cookie_payload = {
            "workspaces": [{"id": "ws-123", "kind": "personal"}],
        }
        engine.session.cookies.get.side_effect = lambda name, default=None: (
            self._encode_cookie_payload(cookie_payload)
            if name == "oai-client-auth-session"
            else default
        )

        consent_response = mock.Mock(status_code=200, headers={}, url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent")
        workspace_response = mock.Mock(status_code=200, headers={}, url="https://auth.openai.com/api/accounts/workspace/select")
        workspace_response.json.return_value = {
            "continue_url": "/sign-in-with-chatgpt/codex/organization",
            "page": {"type": "organization_select"},
            "data": {
                "orgs": [
                    {
                        "id": "org-123",
                        "projects": [{"id": "proj-123"}],
                    }
                ]
            },
        }
        org_response = mock.Mock(
            status_code=302,
            headers={
                "Location": "http://localhost:1455/auth/callback?code=auth-code&state=oauth-state"
            },
        )

        engine.session.get.side_effect = [consent_response]
        engine.session.post.side_effect = [workspace_response, org_response]

        callback_url, workspace_id = engine._resolve_oauth_callback_url(
            "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
        )


class ChatGPTClientRegisterFlowTests(unittest.TestCase):
    @staticmethod
    def _encode_cookie_payload(data):
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _make_engine(self, **kwargs):
        return RefreshTokenRegistrationEngine(
            email_service=DummyEmailService(),
            proxy_url="http://127.0.0.1:7890",
            callback_logger=lambda msg: None,
            **kwargs,
        )

    @mock.patch("platforms.chatgpt.oauth_pkce_client.get_sentinel_token_via_browser", return_value="password-sentinel")
    def test_oauth_pkce_submit_password_uses_username_password_create_sentinel(self, get_sentinel_token_via_browser):
        from platforms.chatgpt.oauth_pkce_client import OAuthPkceClient

        client = OAuthPkceClient(proxy=None, log_fn=lambda _msg: None)
        client._device_id = "device-fixed"
        client.session = mock.Mock()
        client.session.post.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={"continue_url": "https://auth.openai.com/email-verification"}),
        )

        continue_url = client.submit_password("user@example.com", "Secret123!")

        self.assertEqual(continue_url, "https://auth.openai.com/email-verification")
        get_sentinel_token_via_browser.assert_called_once_with(
            flow="username_password_create",
            proxy=None,
            page_url="https://auth.openai.com/create-account/password",
            headless=True,
            device_id="device-fixed",
            log_fn=mock.ANY,
        )
        self.assertEqual(
            client.session.post.call_args.kwargs["headers"]["openai-sentinel-token"],
            "password-sentinel",
        )

    @mock.patch("platforms.chatgpt.oauth_pkce_client.get_sentinel_token_via_browser", return_value="password-sentinel")
    def test_oauth_pkce_submit_password_follows_continue_url_before_post(self, get_sentinel_token_via_browser):
        from platforms.chatgpt.oauth_pkce_client import OAuthPkceClient

        client = OAuthPkceClient(proxy=None, log_fn=lambda _msg: None)
        client._device_id = "device-fixed"
        client.session = mock.Mock()
        client.session.get.return_value = mock.Mock(status_code=200)
        client.session.post.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={"continue_url": "https://auth.openai.com/email-verification"}),
        )

        client.submit_password(
            "user@example.com",
            "Secret123!",
            continue_url="https://auth.openai.com/create-account/password",
        )

        client.session.get.assert_called_once_with(
            "https://auth.openai.com/create-account/password",
            timeout=15,
        )
        get_sentinel_token_via_browser.assert_called_once()

    def test_send_email_otp_uses_post_request(self):
        client = ChatGPTClient(proxy=None, verbose=False, browser_mode=False)
        client._log = lambda _msg: None
        client._browser_pause = mock.Mock()
        client._headers = mock.Mock(return_value={"x-test": "1"})
        client.session = mock.Mock()
        client.session.post.return_value = mock.Mock(status_code=200)

        ok = client.send_email_otp()

        self.assertTrue(ok)
        client.session.post.assert_called_once()
        client.session.get.assert_not_called()

    def test_register_flow_does_not_resend_otp_when_starting_on_email_verification(self):
        client = ChatGPTClient(proxy=None, verbose=False, browser_mode=False)
        client._log = lambda _msg: None

        email_adapter = mock.Mock()
        email_adapter.wait_for_verification_code.return_value = "123456"

        states = iter(
            [
                FlowState(
                    page_type="email_otp_verification",
                    method="GET",
                    current_url="https://auth.openai.com/email-verification",
                ),
                FlowState(
                    page_type="about_you",
                    method="POST",
                    current_url="https://auth.openai.com/about-you",
                ),
                FlowState(
                    page_type="oauth_callback",
                    method="GET",
                    current_url="https://chatgpt.com/api/auth/callback/openai?code=ok",
                ),
            ]
        )

        client.visit_homepage = mock.Mock(return_value=True)
        client.get_csrf_token = mock.Mock(return_value="csrf-token")
        client.signin = mock.Mock(return_value="https://auth.openai.com/oauth/authorize")
        client.authorize = mock.Mock(return_value="https://auth.openai.com/email-verification")
        client.send_email_otp = mock.Mock(return_value=True)
        client.verify_email_otp = mock.Mock(
            return_value=(
                True,
                FlowState(
                    page_type="about_you",
                    method="POST",
                    current_url="https://auth.openai.com/about-you",
                ),
            )
        )
        client.create_account = mock.Mock(
            return_value=(
                True,
                FlowState(
                    page_type="oauth_callback",
                    method="GET",
                    current_url="https://chatgpt.com/api/auth/callback/openai?code=ok",
                ),
            )
        )
        client._state_from_url = mock.Mock(side_effect=lambda _url: next(states))

        ok, message = client.register_complete_flow(
            "user@example.com",
            "Secret123!",
            "Jamie",
            "Taylor",
            "1990-01-01",
            email_adapter,
        )

        self.assertTrue(ok)
        self.assertEqual(message, "注册成功")
        client.send_email_otp.assert_not_called()
        email_adapter.wait_for_verification_code.assert_called_once_with(
            "user@example.com",
            timeout=300,
        )

    def test_get_workspace_id_from_api_accepts_nested_data_workspaces(self):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get.return_value = None
        engine._browser_frontend_state = {"page_type": "consent"}

        with mock.patch.object(
            engine,
            "_submit_browser_form",
            return_value=(
                {
                    "status": 200,
                    "body": json.dumps(
                        {
                            "data": {
                                "workspaces": [
                                    {
                                        "id": "44444444-4444-4444-4444-444444444444",
                                        "kind": "personal",
                                    }
                                ]
                            }
                        }
                    ),
                },
                {},
            ),
        ):
            workspace_id = engine._get_workspace_id_from_api()

        self.assertEqual(workspace_id, "44444444-4444-4444-4444-444444444444")
        engine.session.get.assert_not_called()

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_post_otp_add_phone_recovers_workspace_from_consent_html(self, _sleep):
        engine = self._make_engine()
        engine._post_otp_page_type = "add_phone"
        engine._post_otp_continue_url = "https://auth.openai.com/add-phone"
        engine._device_id = "device-fixed"

        consent_response = mock.Mock(
            status_code=200,
            url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        workspace_api_response = mock.Mock(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
        )
        workspace_api_response.json.side_effect = ValueError("not json")
        workspace_api_response.text = "<html>not-json</html>"

        consent_html_response = mock.Mock(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                '<html><body>'
                'streamController.enqueue("{\\"workspaces\\":[{\\"id\\":\\"11111111-1111-1111-1111-111111111111\\",\\"kind\\":\\"personal\\"}],'
                '\\"openai_client_id\\":\\"client-123\\"}")'
                "</body></html>"
            ),
        )

        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.get.side_effect = [
            consent_response,
            workspace_api_response,
            consent_html_response,
        ]
        session.cookies = mock.Mock()
        session.cookies.get_dict.return_value = {
            "oai-client-auth-session": self._encode_cookie_payload({"email": "user@example.com"})
        }
        session.cookies.get.side_effect = lambda name, default=None: (
            self._encode_cookie_payload({"email": "user@example.com"})
            if name == "oai-client-auth-session"
            else default
        )
        engine.session = session

        result = SignupFormResult(success=False)
        registration_result = mock.Mock()

        with mock.patch.object(
            engine,
            "_select_workspace",
            return_value="http://localhost:1455/auth/callback?code=auth-code&state=oauth-state",
        ) as select_workspace:
            with mock.patch.object(
                engine,
                "_follow_redirects",
                return_value="http://localhost:1455/auth/callback?code=auth-code&state=oauth-state",
            ):
                with mock.patch.object(
                    engine,
                    "_handle_oauth_callback",
                    return_value={"account_id": "acct-1", "access_token": "token-1"},
                ):
                    ok = engine._complete_post_otp_flow(registration_result)

        self.assertTrue(ok)
        select_workspace.assert_called_once_with("11111111-1111-1111-1111-111111111111")
        self.assertEqual(registration_result.workspace_id, "11111111-1111-1111-1111-111111111111")

    def test_get_workspace_id_from_consent_html_prefers_browser_state_when_available(self):
        engine = self._make_engine()
        engine._browser_frontend_state = {"page_type": "consent"}
        engine.session = mock.Mock()

        browser_html = (
            '<html><body>'
            'streamController.enqueue("{\\"workspaces\\":[{\\"id\\":\\"33333333-3333-3333-3333-333333333333\\",\\"kind\\":\\"personal\\"}]}")'
            "</body></html>"
        )
        browser_result = {
            "status": 200,
            "body": browser_html,
            "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            "navigation_chain": ["https://auth.openai.com/sign-in-with-chatgpt/codex/consent"],
            "cookies": [],
            "storage_state": {},
        }

        with mock.patch.object(engine, "_submit_browser_form", return_value=(browser_result, {})) as submit_browser:
            workspace_id = engine._get_workspace_id_from_consent_html(
                "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
            )

        self.assertEqual(workspace_id, "33333333-3333-3333-3333-333333333333")
        submit_browser.assert_called_once()
        engine.session.get.assert_not_called()

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_post_otp_add_phone_falls_back_to_phone_when_bypass_cannot_resolve_workspace(self, _sleep):
        engine = self._make_engine()
        engine._post_otp_page_type = "add_phone"
        engine._post_otp_continue_url = "https://auth.openai.com/add-phone"
        engine._device_id = "device-fixed"

        consent_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )

        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.get.side_effect = [consent_response]
        session.cookies = mock.Mock()
        session.cookies.get.return_value = None
        engine.session = session

        registration_result = mock.Mock()

        with mock.patch.object(
            engine,
            "_get_workspace_id",
            side_effect=[None, "22222222-2222-2222-2222-222222222222"],
        ) as get_workspace:
            with mock.patch.object(engine, "_get_workspace_id_from_api", return_value=None):
                with mock.patch.object(engine, "_get_workspace_id_from_consent_html", return_value=None):
                    with mock.patch.object(engine, "_handle_phone_verification", return_value=True) as phone_verify:
                        with mock.patch.object(
                            engine,
                            "_select_workspace",
                            return_value="http://localhost:1455/auth/callback?code=auth-code&state=oauth-state",
                        ) as select_workspace:
                            with mock.patch.object(
                                engine,
                                "_follow_redirects",
                                return_value="http://localhost:1455/auth/callback?code=auth-code&state=oauth-state",
                            ):
                                with mock.patch.object(
                                    engine,
                                    "_handle_oauth_callback",
                                    return_value={"account_id": "acct-1", "access_token": "token-1"},
                                ):
                                    ok = engine._complete_post_otp_flow(registration_result)

        self.assertTrue(ok)
        self.assertEqual(get_workspace.call_count, 2)
        phone_verify.assert_called_once()
        select_workspace.assert_called_once_with("22222222-2222-2222-2222-222222222222")
        self.assertEqual(registration_result.workspace_id, "22222222-2222-2222-2222-222222222222")

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_post_phone_workspace_failure_reports_oauth_failure_not_smsbower_failure(self, _sleep):
        engine = self._make_engine()
        engine._post_otp_page_type = "add_phone"
        engine._post_otp_continue_url = "https://auth.openai.com/add-phone"
        engine._device_id = "device-fixed"

        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.get.side_effect = [
            mock.Mock(status_code=403, url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent"),
            mock.Mock(status_code=403, url="https://auth.openai.com/about-you"),
        ]
        session.cookies = mock.Mock()
        session.cookies.get.return_value = None
        engine.session = session

        registration_result = mock.Mock(error_message="")

        with mock.patch.object(engine, "_get_workspace_id", side_effect=[None, "ws-after-phone"]):
            with mock.patch.object(engine, "_handle_phone_verification", return_value=True):
                with mock.patch.object(engine, "_resolve_oauth_callback_via_browser", return_value=""):
                    with mock.patch.object(engine, "_select_workspace", return_value=None):
                        ok = engine._complete_post_otp_flow(registration_result)

        self.assertFalse(ok)
        self.assertIn("手机号已验证通过", registration_result.error_message)
        self.assertIn("OAuth", registration_result.error_message)
        self.assertNotIn("SMSBOWER", registration_result.error_message)

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_post_otp_flow_does_not_restart_login_after_phone_fallback_fails(self, _sleep):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine._token_acquisition_requires_login = True
        engine._post_otp_page_type = "add_phone"
        engine._post_otp_continue_url = "https://auth.openai.com/add-phone"

        registration_result = mock.Mock(error_message="")

        with mock.patch.object(
            engine,
            "_submit_browser_form",
            return_value=(
                {
                    "status": 200,
                    "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                },
                {},
            ),
        ), \
            mock.patch.object(engine, "_get_workspace_id", return_value=None), \
            mock.patch.object(engine, "_get_workspace_id_from_api", return_value=None), \
            mock.patch.object(engine, "_get_workspace_id_from_consent_html", return_value=None), \
            mock.patch.object(engine, "_verify_current_account_identity_for_authorization", return_value=(True, "")), \
            mock.patch.object(engine, "_bootstrap_authorization_context", return_value=True), \
            mock.patch.object(engine, "_resolve_oauth_callback_via_browser", return_value=""), \
            mock.patch.object(engine, "_resolve_oauth_callback_url", return_value=("", "")), \
            mock.patch.object(engine, "_handle_phone_verification", return_value=False) as phone_verify, \
            mock.patch.object(engine, "_restart_login_flow") as restart_login:
            ok = engine._complete_post_otp_flow(registration_result)

        self.assertFalse(ok)
        phone_verify.assert_called_once()
        restart_login.assert_not_called()
        self.assertIn("要求绑定手机号", registration_result.error_message)

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_post_otp_phone_success_creates_about_you_account_before_workspace(self, _sleep):
        engine = self._make_engine()
        engine._post_otp_page_type = "add_phone"
        engine._post_otp_continue_url = "https://auth.openai.com/add-phone"

        consent_response = mock.Mock(
            status_code=403,
            url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        about_response = mock.Mock(
            status_code=200,
            url="https://auth.openai.com/add-phone",
        )
        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.get.side_effect = [consent_response, about_response]
        session.cookies = mock.Mock()
        session.cookies.get.return_value = None
        engine.session = session

        registration_result = mock.Mock()

        def phone_success():
            engine._post_otp_page_type = "about_you"
            engine._post_otp_continue_url = "https://auth.openai.com/about-you"
            return True

        def create_about_you():
            engine._post_otp_page_type = "consent"
            engine._post_otp_continue_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
            return engine._post_otp_continue_url

        with mock.patch.object(engine, "_get_workspace_id", side_effect=[None, "ws-1"]) as get_workspace:
            with mock.patch.object(engine, "_get_workspace_id_from_api", return_value=None):
                with mock.patch.object(engine, "_get_workspace_id_from_consent_html", return_value=None):
                    with mock.patch.object(engine, "_handle_phone_verification", side_effect=phone_success):
                        with mock.patch.object(
                            engine,
                            "_create_account_during_oauth_if_needed",
                            side_effect=create_about_you,
                        ) as create_account:
                            with mock.patch.object(engine, "_select_workspace", return_value="callback-url"):
                                with mock.patch.object(engine, "_follow_redirects", return_value="callback-url"):
                                    with mock.patch.object(
                                        engine,
                                        "_handle_oauth_callback",
                                        return_value={"account_id": "acct-1", "access_token": "token-1"},
                                    ):
                                        ok = engine._complete_post_otp_flow(registration_result)

        self.assertTrue(ok)
        create_account.assert_called_once()
        self.assertEqual(get_workspace.call_count, 2)

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_complete_post_otp_flow_goes_directly_to_phone_when_add_phone_required(self, _sleep):
        engine = self._make_engine()
        engine._post_otp_page_type = "add_phone"
        engine._post_otp_continue_url = "https://auth.openai.com/add-phone"
        engine.session = mock.Mock()

        registration_result = mock.Mock()

        with mock.patch.object(engine, "_submit_browser_form") as submit_browser_form, \
            mock.patch.object(engine, "_get_workspace_id") as get_workspace, \
            mock.patch.object(engine, "_get_workspace_id_from_api") as get_workspace_api, \
            mock.patch.object(engine, "_get_workspace_id_from_consent_html") as get_workspace_html, \
            mock.patch.object(engine, "_bootstrap_authorization_context") as bootstrap_context, \
            mock.patch.object(engine, "_resolve_oauth_callback_via_browser") as resolve_browser, \
            mock.patch.object(engine, "_resolve_oauth_callback_url") as resolve_http, \
            mock.patch.object(engine, "_handle_phone_verification", return_value=False) as phone_verify:
            ok = engine._complete_post_otp_flow(registration_result)

        self.assertFalse(ok)
        phone_verify.assert_called_once()
        submit_browser_form.assert_not_called()
        get_workspace.assert_not_called()
        get_workspace_api.assert_not_called()
        get_workspace_html.assert_not_called()
        bootstrap_context.assert_not_called()
        resolve_browser.assert_not_called()
        resolve_http.assert_not_called()

    def test_complete_post_otp_flow_prefers_browser_callback_resolution(self):
        engine = self._make_engine()
        engine._browser_frontend_state = {"page_type": "consent"}
        engine._post_otp_page_type = "consent"
        engine._post_otp_continue_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()
        engine.session.cookies.get.return_value = None

        registration_result = mock.Mock()

        with mock.patch.object(engine, "_get_workspace_id", return_value="ws-browser-1"):
            with mock.patch.object(
                engine,
                "_resolve_oauth_callback_via_browser",
                return_value="http://localhost:1455/auth/callback?code=auth-code&state=oauth-state",
            ) as browser_resolver:
                with mock.patch.object(engine, "_select_workspace") as select_workspace:
                    with mock.patch.object(engine, "_follow_redirects") as follow_redirects:
                        with mock.patch.object(
                            engine,
                            "_handle_oauth_callback",
                            return_value={"account_id": "acct-1", "access_token": "token-1"},
                        ):
                            ok = engine._complete_post_otp_flow(registration_result)

        self.assertTrue(ok)
        browser_resolver.assert_called_once()
        select_workspace.assert_not_called()
        follow_redirects.assert_not_called()
        self.assertEqual(registration_result.workspace_id, "ws-browser-1")

    def test_create_account_during_oauth_uses_browser_form_not_http_session(self):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.post.side_effect = AssertionError("HTTP session must not create about-you account")

        with mock.patch(
            "platforms.chatgpt.refresh_token_registration_engine.generate_random_user_info",
            return_value={"name": "Ada Lovelace", "birthdate": "1990-01-01"},
        ):
            with mock.patch.object(
                engine,
                "_submit_browser_form",
                return_value=(
                    {
                        "status": 200,
                        "body": json.dumps(
                            {
                                "continue_url": "/sign-in-with-chatgpt/codex/consent",
                                "page": {"type": "consent"},
                            }
                        ),
                    },
                    {
                        "continue_url": "/sign-in-with-chatgpt/codex/consent",
                        "page": {"type": "consent"},
                    },
                ),
            ) as submit_browser_form:
                continue_url = engine._create_account_during_oauth_if_needed()

        self.assertEqual(
            continue_url,
            "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        submit_browser_form.assert_called_once()
        self.assertEqual(submit_browser_form.call_args.kwargs["form_type"], "create_account")
        self.assertTrue(submit_browser_form.call_args.kwargs["update_post_otp_state"])
        engine.session.post.assert_not_called()

    def test_create_account_during_oauth_does_not_treat_already_exists_as_success_without_consent_state(self):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine._post_otp_page_type = "about_you"
        engine._post_otp_continue_url = "https://auth.openai.com/about-you"

        with mock.patch(
            "platforms.chatgpt.refresh_token_registration_engine.generate_random_user_info",
            return_value={"name": "Ada Lovelace", "birthdate": "1990-01-01"},
        ):
            with mock.patch.object(
                engine,
                "_submit_browser_form",
                return_value=(
                    {
                        "status": 400,
                        "body": json.dumps({"error": {"code": "already_exists"}}),
                        "final_url": "https://auth.openai.com/about-you",
                        "page_type": "about_you",
                        "continue_url": "",
                    },
                    {"error": {"code": "already_exists"}},
                ),
            ):
                continue_url = engine._create_account_during_oauth_if_needed()

        self.assertEqual(continue_url, "")

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_complete_post_otp_flow_stops_when_about_you_never_advances_to_callback(self, _sleep):
        engine = self._make_engine()
        engine._post_otp_page_type = "about_you"
        engine._post_otp_continue_url = "https://auth.openai.com/about-you"

        session = mock.Mock()
        session.get.return_value = mock.Mock(
            status_code=200,
            url="https://auth.openai.com/about-you",
        )
        session.cookies = mock.Mock()
        session.cookies.get.return_value = None
        engine.session = session

        registration_result = mock.Mock()

        with mock.patch.object(
            engine,
            "_create_account_during_oauth_if_needed",
            return_value="",
        ) as create_account:
            with mock.patch.object(engine, "_get_workspace_id") as get_workspace:
                with mock.patch.object(engine, "_get_workspace_id_from_api") as get_workspace_api:
                    with mock.patch.object(engine, "_get_workspace_id_from_consent_html") as get_workspace_html:
                        ok = engine._complete_post_otp_flow(registration_result)

        self.assertFalse(ok)
        self.assertIn("about-you", registration_result.error_message)
        self.assertIn("callback", registration_result.error_message)
        create_account.assert_called_once()
        get_workspace.assert_not_called()
        get_workspace_api.assert_not_called()
        get_workspace_html.assert_not_called()

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_complete_post_otp_flow_switches_to_login_auth_when_about_you_reports_already_exists_without_consent(self, _sleep):
        """A bare already_exists response means the basic account exists.
        Continue via login/OAuth instead of workspace/consent resolution."""
        engine = self._make_engine()
        engine.email = "used@example.com"
        engine._post_otp_page_type = "about_you"
        engine._post_otp_continue_url = "https://auth.openai.com/about-you"

        session = mock.Mock()
        session.cookies = mock.Mock()
        session.cookies.get.return_value = None
        engine.session = session

        registration_result = mock.Mock()

        def create_account_already_exists():
            engine._about_you_create_account_already_exists_without_consent = True
            return ""

        with mock.patch.object(
            engine,
            "_create_account_during_oauth_if_needed",
            side_effect=create_account_already_exists,
        ) as create_account:
            with mock.patch.object(engine, "_restart_login_flow") as restart_login:
                with mock.patch.object(engine, "_complete_token_exchange") as complete_token:
                    with mock.patch.object(
                        engine, "_get_workspace_id", return_value="ws-existing"
                    ) as get_workspace:
                        ok = engine._complete_post_otp_flow(
                            registration_result, exchange_token=False
                        )

        self.assertTrue(ok)
        self.assertTrue(engine._is_existing_account)
        self.assertTrue(engine._token_acquisition_requires_login)
        create_account.assert_called_once()
        restart_login.assert_not_called()
        complete_token.assert_not_called()
        get_workspace.assert_not_called()

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_basic_account_stage_accepts_plain_chatgpt_callback_without_workspace(self, _sleep):
        engine = self._make_engine()
        engine.email = "new@example.com"
        engine._post_otp_page_type = "about_you"
        engine._post_otp_continue_url = "https://auth.openai.com/about-you"

        session = mock.Mock()
        session.cookies = mock.Mock()
        session.cookies.get.return_value = None
        engine.session = session

        registration_result = mock.Mock(error_message="")
        plain_chatgpt_callback = (
            "https://chatgpt.com/api/auth/callback/openai?"
            "code=ac_plain_account_creation_code"
        )

        with mock.patch.object(
            engine,
            "_create_account_during_oauth_if_needed",
            return_value=plain_chatgpt_callback,
        ) as create_account:
            with mock.patch.object(engine, "_get_workspace_id") as get_workspace:
                with mock.patch.object(engine, "_get_workspace_id_from_api") as get_workspace_api:
                    with mock.patch.object(engine, "_get_workspace_id_from_consent_html") as get_workspace_html:
                        ok = engine._complete_post_otp_flow(
                            registration_result, exchange_token=False
                        )

        self.assertTrue(ok)
        self.assertTrue(engine._token_acquisition_requires_login)
        self.assertEqual(engine._post_otp_continue_url, plain_chatgpt_callback)
        create_account.assert_called_once()
        get_workspace.assert_not_called()
        get_workspace_api.assert_not_called()
        get_workspace_html.assert_not_called()

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_complete_post_otp_flow_reports_unbootstrapped_account_after_relogin(self, _sleep):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine._token_acquisition_requires_login = True
        engine._post_otp_page_type = "consent"
        engine._post_otp_continue_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"

        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.cookies = mock.Mock()
        auth_cookie = self._encode_cookie_payload(
            {
                "email": "user@example.com",
                "email_verification_mode": "onboard",
                "workspaces": [],
            }
        )
        session.cookies.get_dict.return_value = {"oai-client-auth-session": auth_cookie}
        session.cookies.get.side_effect = lambda name, default=None: (
            auth_cookie if name == "oai-client-auth-session" else default
        )
        engine.session = session

        registration_result = mock.Mock()

        with mock.patch.object(engine, "_get_workspace_id_from_api", return_value=None) as workspace_api:
            with mock.patch.object(engine, "_get_workspace_id_from_consent_html", return_value=None) as consent_html:
                with mock.patch.object(engine, "_select_workspace") as select_workspace:
                    ok = engine._complete_post_otp_flow(registration_result)

        self.assertFalse(ok)
        self.assertIn("account_not_fully_bootstrapped", registration_result.error_message)
        workspace_api.assert_called_once()
        consent_html.assert_called_once()
        select_workspace.assert_not_called()

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_complete_post_otp_flow_bootstraps_context_then_restarts_login_once(self, _sleep):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine._token_acquisition_requires_login = True
        engine._post_otp_page_type = "consent"
        engine._post_otp_continue_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"

        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.cookies = mock.Mock()
        auth_cookie = self._encode_cookie_payload(
            {
                "email": "user@example.com",
                "email_verification_mode": "onboard",
                "workspaces": [],
            }
        )
        session.cookies.get_dict.return_value = {"oai-client-auth-session": auth_cookie}
        session.cookies.get.side_effect = lambda name, default=None: (
            auth_cookie if name == "oai-client-auth-session" else default
        )
        engine.session = session

        registration_result = mock.Mock()

        def complete_after_relogin(result):
            result.account_id = "acct-1"
            result.access_token = "at"
            result.refresh_token = "rt"
            result.id_token = "id"
            return True

        with mock.patch.object(engine, "_get_workspace_id", return_value=None):
            with mock.patch.object(engine, "_get_workspace_id_from_api", return_value=None):
                with mock.patch.object(engine, "_get_workspace_id_from_consent_html", return_value=None):
                    with mock.patch.object(engine, "_bootstrap_authorization_context", return_value=True) as bootstrap:
                        with mock.patch.object(engine, "_restart_login_flow", return_value=(True, "")) as restart_login:
                            with mock.patch.object(
                                engine,
                                "_complete_token_exchange",
                                side_effect=complete_after_relogin,
                            ) as complete_token:
                                with mock.patch.object(engine, "_select_workspace") as select_workspace:
                                    ok = engine._complete_post_otp_flow(registration_result)

        self.assertTrue(ok)
        self.assertEqual(registration_result.refresh_token, "rt")
        bootstrap.assert_called_once()
        restart_login.assert_called_once()
        complete_token.assert_called_once_with(registration_result)
        select_workspace.assert_not_called()

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_complete_post_otp_flow_bootstraps_context_before_workspace_resolution_when_identity_verified(self, _sleep):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine._token_acquisition_requires_login = True
        engine._post_otp_page_type = "consent"
        engine._post_otp_continue_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"

        auth_cookie = self._encode_cookie_payload(
            {
                "email": "user@example.com",
                "email_verification_mode": "onboard",
                "workspaces": [],
            }
        )
        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.cookies = mock.Mock()
        session.cookies.get_dict.return_value = {"oai-client-auth-session": auth_cookie}
        session.cookies.get.side_effect = lambda name, default=None: (
            auth_cookie if name == "oai-client-auth-session" else default
        )
        engine.session = session

        calls = []
        registration_result = mock.Mock()

        def get_workspace():
            calls.append("get_workspace")
            return "ws-after-bootstrap" if calls.count("get_workspace") > 1 else None

        def bootstrap():
            calls.append("bootstrap")
            return True

        with mock.patch.object(engine, "_get_workspace_id", side_effect=get_workspace):
            with mock.patch.object(engine, "_bootstrap_authorization_context", side_effect=bootstrap) as bootstrap_context:
                with mock.patch.object(engine, "_get_workspace_id_from_api") as workspace_api:
                    with mock.patch.object(engine, "_get_workspace_id_from_consent_html") as consent_html:
                        with mock.patch.object(engine, "_resolve_oauth_callback_via_browser", return_value="") as browser_resolver:
                            with mock.patch.object(engine, "_select_workspace", return_value="callback-url") as select_workspace:
                                with mock.patch.object(engine, "_follow_redirects", return_value="callback-url"):
                                    with mock.patch.object(
                                        engine,
                                        "_handle_oauth_callback",
                                        return_value={"account_id": "acct-1", "access_token": "token-1"},
                                    ):
                                        ok = engine._complete_post_otp_flow(registration_result)

        self.assertTrue(ok)
        self.assertEqual(calls[:2], ["get_workspace", "bootstrap"])
        bootstrap_context.assert_called_once()
        workspace_api.assert_not_called()
        consent_html.assert_not_called()
        browser_resolver.assert_called_once_with(
            "ws-after-bootstrap",
            "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        )
        select_workspace.assert_called_once_with("ws-after-bootstrap")

    @mock.patch("platforms.chatgpt.refresh_token_registration_engine.time.sleep", return_value=None)
    def test_complete_post_otp_flow_refuses_context_bootstrap_when_session_email_mismatches_target(self, _sleep):
        engine = self._make_engine()
        engine.email = "user@example.com"
        engine._token_acquisition_requires_login = True
        engine._post_otp_page_type = "consent"
        engine._post_otp_continue_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"

        auth_cookie = self._encode_cookie_payload(
            {
                "email": "other@example.com",
                "email_verification_mode": "onboard",
                "workspaces": [],
            }
        )
        session = mock.Mock()
        session.headers = {"User-Agent": "test-agent"}
        session.cookies = mock.Mock()
        session.cookies.get_dict.return_value = {"oai-client-auth-session": auth_cookie}
        session.cookies.get.side_effect = lambda name, default=None: (
            auth_cookie if name == "oai-client-auth-session" else default
        )
        engine.session = session

        registration_result = mock.Mock()

        with mock.patch.object(engine, "_get_workspace_id", return_value=None):
            with mock.patch.object(engine, "_bootstrap_authorization_context") as bootstrap_context:
                with mock.patch.object(engine, "_get_workspace_id_from_api") as workspace_api:
                    with mock.patch.object(engine, "_get_workspace_id_from_consent_html") as consent_html:
                        ok = engine._complete_post_otp_flow(registration_result)

        self.assertFalse(ok)
        self.assertIn("account_identity_mismatch", registration_result.error_message)
        bootstrap_context.assert_not_called()
        workspace_api.assert_not_called()
        consent_html.assert_not_called()

    def test_resolve_oauth_callback_via_browser_stops_on_retry_only_error_page(self):
        engine = self._make_engine()
        engine._browser_frontend_state = {"cookies": [], "storage_state": {}}

        error_result = {
            "status": 200,
            "body": "<html><body><button>重试</button></body></html>",
            "cookies": [],
            "challenge_passed": True,
            "final_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            "page_type": "consent",
            "continue_url": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            "navigation_chain": ["https://auth.openai.com/sign-in-with-chatgpt/codex/consent"],
            "storage_state": {},
            "visible_buttons": ["重试"],
        }

        with mock.patch.object(engine, "_submit_browser_form", return_value=(error_result, {})) as submit_browser_form:
            callback_url = engine._resolve_oauth_callback_via_browser(
                "",
                "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
            )

        self.assertEqual(callback_url, "")
        submit_browser_form.assert_called_once()
        self.assertIn("consent_authorize encountered retry-only error page", "\n".join(engine.logs))

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.build_sentinel_token",
        return_value='{"source":"pow"}',
    )
    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine._run_browser_for_page",
        return_value={"sentinel_token": '{"source":"browser"}', "cookies": []},
    )
    def test_check_sentinel_prefers_browser_for_register_and_create_account_flows(
        self, mock_browser_run, mock_pow_token
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()

        token = engine._check_sentinel("device-fixed", flow="username_password_create")
        self.assertEqual(token, '{"source":"browser"}')
        mock_browser_run.assert_called_once()
        mock_pow_token.assert_not_called()

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.build_sentinel_token",
        return_value='{"source":"pow"}',
    )
    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine._run_browser_for_page",
        return_value={
            "sentinel_token": '{"source":"browser"}',
            "cookies": [
                {"name": "auth-state", "value": "detached", "domain": ".openai.com", "path": "/"},
                {"name": "__cf_bm", "value": "browser-bm", "domain": ".openai.com", "path": "/"},
            ],
        },
    )
    def test_check_sentinel_does_not_merge_detached_browser_cookies(
        self, mock_browser_run, mock_pow_token
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()
        engine.session.cookies = mock.Mock()

        token = engine._check_sentinel("device-fixed", flow="username_password_create")

        self.assertEqual(token, '{"source":"browser"}')
        mock_browser_run.assert_called_once()
        self.assertFalse(engine.session.cookies.set.called)
        mock_pow_token.assert_not_called()

    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine.build_sentinel_token",
        return_value='{"source":"pow"}',
    )
    @mock.patch(
        "platforms.chatgpt.refresh_token_registration_engine._run_browser_for_page",
        return_value=None,
    )
    def test_check_sentinel_falls_back_to_pow_when_browser_token_missing(
        self, mock_browser_run, mock_pow_token
    ):
        engine = self._make_engine()
        engine.session = mock.Mock()

        token = engine._check_sentinel("device-fixed", flow="oauth_create_account")
        self.assertEqual(token, '{"source":"pow"}')
        mock_browser_run.assert_called_once()
        mock_pow_token.assert_called_once_with(
            engine.session, "device-fixed", flow="oauth_create_account"
        )


if __name__ == "__main__":
    unittest.main()
