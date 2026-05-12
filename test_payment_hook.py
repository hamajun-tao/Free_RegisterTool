import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock
from fastapi import BackgroundTasks, HTTPException

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.tasks import (
    _auto_pay_after_register,
    _make_pre_oauth_auto_pay_hook,
    _build_payment_idempotency_key,
    create_register_task,
    RegisterTaskRequest,
)
from core.config_store import config_store
from platforms.chatgpt.payment_auto import _build_card_cfg, run_payment_from_config_store, PaymentError


class MockAccount:
    def __init__(self):
        self.id = 9999
        self.platform = "chatgpt"
        self.email = "test_user_123@example.com"
        self.password = "TestPass123!"
        self.status = "registered"
        self.extra_json = json.dumps({
            "access_token": "fake_access_token_123",
            "session_token": "fake_session_token_123",
            "oai_device_id": "test-device-id-1234",
            "cookie_header": "_puid=xxx;",
        })


class TestPaymentFlow(unittest.TestCase):
    def setUp(self):
        self._config_keys = [
            "payment_auto_plan",
            "payment_plus_flow_order",
            "payment_method",
            "payment_card_number",
            "payment_card_cvc",
            "payment_card_exp_month",
            "payment_card_exp_year",
            "payment_gopay_phone",
            "payment_gopay_pin",
            "payment_gopay_otp_file",
            "payment_paypal_email",
            "payment_paypal_password",
            "payment_python_executable",
            "payment_captcha_api_url",
            "payment_captcha_key",
            "payment_captcha_validate_online",
            "payment_billing_name",
            "payment_billing_address",
            "payment_billing_city",
            "payment_billing_state",
            "payment_billing_zip",
            "payment_billing_country",
            "payment_billing_currency",
            "payment_vlm_base_url",
            "payment_vlm_api_key",
            "payment_vlm_model",
            "payment_vlm_timeout_s",
        ]
        self._config_snapshot = {key: config_store.get(key, "") for key in self._config_keys}
        self.account = MockAccount()
        self.task_id = "test-coverage-001"
        config_store.set_many({
            "payment_plus_flow_order": "after_oauth",
            "payment_captcha_api_url": "https://api.yescaptcha.com",
            "payment_captcha_key": "real-captcha-key-for-test",
            "payment_captcha_validate_online": "0",
            "payment_python_executable": r"E:\ctf-pay-runtime\Scripts\python.exe",
            "payment_billing_name": "Valid Test User",
            "payment_billing_address": "350 Fifth Avenue",
            "payment_billing_city": "New York",
            "payment_billing_state": "NY",
            "payment_billing_zip": "10118",
            "payment_billing_country": "US",
            "payment_billing_currency": "USD",
            # 清空 VLM 配置，避免生产配置在非 VLM 测试中触发运行时检查
            "payment_vlm_base_url": "",
            "payment_vlm_api_key": "",
            "payment_vlm_model": "",
            "payment_vlm_timeout_s": "",
        })

    def tearDown(self):
        config_store.set_many(self._config_snapshot)

    def test_01_build_cfg_for_card(self):
        cfg = _build_card_cfg(
            plan_name="chatgptplusplan",
            access_token="tk", session_token="st", cookie_header="ck", device_id="id",
            card_number="424242", card_exp_month="12", card_exp_year="2027", card_cvc="123",
            billing_name="John", billing_country="US", billing_currency="usd",
            billing_address="123 St", billing_city="NY", billing_state="NY", billing_zip="10001",
            team_workspace_name="", team_seat_quantity=5, captcha_api_url="", captcha_key="",
            proxy_url="http://127.0.0.1",
        )
        self.assertEqual(cfg["cards"][0]["number"], "424242")
        self.assertEqual(cfg["cards"][0]["address"]["line1"], "123 St")
        self.assertEqual(cfg["cards"][0]["address"]["postal_code"], "10001")
        self.assertEqual(cfg["billing"]["country"], "US")
        self.assertEqual(cfg["billing"]["line1"], "123 St")
        self.assertEqual(cfg["fresh_checkout"]["plan"]["plan_name"], "chatgptplusplan")
        self.assertEqual(cfg["fresh_checkout"]["request_style"], "auto")
        self.assertTrue(cfg["fresh_checkout"]["fallback_to_modern"])
        self.assertEqual(cfg["proxy"], "http://127.0.0.1")
        self.assertEqual(cfg["captcha"]["api_key"], "")
        self.assertEqual(cfg["captcha"]["client_key"], "")
        self.assertNotIn("gopay", cfg)
        self.assertNotIn("paypal", cfg)

    def test_02_build_cfg_for_gopay_http(self):
        cfg = _build_card_cfg(
            plan_name="chatgptteamplan",
            access_token="tk", session_token="st", cookie_header="ck", device_id="id",
            card_number="", card_exp_month="", card_exp_year="", card_cvc="",
            billing_name="", billing_country="ID", billing_currency="idr",
            billing_address="", billing_city="", billing_state="", billing_zip="",
            team_workspace_name="MyTeam", team_seat_quantity=3, captcha_api_url="", captcha_key="",
            gopay_phone="81234567", gopay_pin="123456", gopay_otp_url="http://127.0.0.1/latest",
            proxy_url="",
        )
        self.assertIn("gopay", cfg)
        self.assertEqual(cfg["gopay"]["country_code"], "62")
        self.assertEqual(cfg["gopay"]["phone_number"], "81234567")
        self.assertEqual(cfg["gopay"]["pin"], "123456")
        self.assertEqual(cfg["gopay"]["otp"]["url"], "http://127.0.0.1/latest")
        self.assertEqual(cfg["fresh_checkout"]["plan"]["seat_quantity"], 3)

    def test_03_build_cfg_for_paypal(self):
        cfg = _build_card_cfg(
            plan_name="chatgptplusplan",
            access_token="tk", session_token="st", cookie_header="ck", device_id="id",
            card_number="", card_exp_month="", card_exp_year="", card_cvc="",
            billing_name="", billing_country="DE", billing_currency="eur",
            billing_address="", billing_city="", billing_state="", billing_zip="",
            team_workspace_name="", team_seat_quantity=5, captcha_api_url="", captcha_key="",
            paypal_email="pay@example.com", paypal_password="pwd",
            proxy_url="",
        )
        self.assertIn("paypal", cfg)
        self.assertEqual(cfg["paypal"]["email"], "pay@example.com")
        self.assertEqual(cfg["paypal"]["password"], "pwd")

    @patch("platforms.chatgpt.payment_auto.subprocess.Popen")
    @patch("core.db.Session")
    def test_04_full_auto_pay_hook_success(self, mock_db_sess, mock_popen):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "card",
            "payment_card_number": "1111222233334444",
        })
        mock_process = MagicMock()
        mock_process.stdout.__iter__.return_value = iter([
            'CARD_RESULT_JSON:{"receipt_url": "https://pay.stripe.com/receipts/xxx", "state": "success"}\n',
        ])
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process
        mock_db_instance = mock_db_sess.return_value.__enter__.return_value
        mock_acct_db = MagicMock()
        mock_db_instance.exec.return_value.first.return_value = mock_acct_db

        _auto_pay_after_register(self.task_id, self.account, {})

        self.assertEqual(self.account.status, "subscribed")
        account_extra = json.loads(self.account.extra_json)
        self.assertEqual(account_extra["auto_pay_plan"], "plus")
        self.assertEqual(account_extra["auto_pay_state"], "succeeded")
        self.assertEqual(account_extra["auto_pay_receipt"], "https://pay.stripe.com/receipts/xxx")

    @patch("platforms.chatgpt.payment_auto.subprocess.Popen")
    def test_05_full_auto_pay_hook_gopay_cli_args(self, mock_popen):
        config_store.set_many({
            "payment_auto_plan": "team",
            "payment_provider": "",
            "payment_method": "gopay",
            "payment_promo_proxy_url": "",
            "payment_gopay_phone": "888",
            "payment_gopay_pin": "123",
            "payment_gopay_otp_file": "/tmp/test_otp.txt",
        })
        mock_process = MagicMock()
        mock_process.stdout.__iter__.return_value = iter([])
        mock_process.stderr.read.return_value = "mock gopay failure"
        mock_process.wait.return_value = 1
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        _auto_pay_after_register(self.task_id, self.account, {})

        call_args = mock_popen.call_args[0][0]
        self.assertIn("--gopay", call_args)
        self.assertIn("--gopay-otp-file", call_args)
        self.assertIn("/tmp/test_otp.txt", call_args)

    @patch("platforms.chatgpt.payment_auto.subprocess.Popen")
    def test_06_full_auto_pay_hook_uses_configured_python_executable(self, mock_popen):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_python_executable": r"E:\ctf-pay-runtime\Scripts\python.exe",
        })
        mock_process = MagicMock()
        mock_process.stdout.__iter__.return_value = iter([
            'CARD_RESULT_JSON:{"receipt_url": "https://pay.stripe.com/receipts/paypal", "state": "success"}\n',
        ])
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        _auto_pay_after_register(self.task_id, self.account, {})

        call_args = mock_popen.call_args[0][0]
        call_kwargs = mock_popen.call_args.kwargs
        self.assertEqual(call_args[0], r"E:\ctf-pay-runtime\Scripts\python.exe")
        self.assertIn("--paypal", call_args)
        self.assertEqual(call_kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(call_kwargs["env"]["PYTHONUTF8"], "1")

    @patch("platforms.chatgpt.payment_auto.run_payment_from_config_store")
    def test_07_auto_pay_passes_runtime_proxy_to_payment(self, mock_run_payment):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
        })
        mock_run_payment.return_value = MagicMock(success=False, state="failed", error="mock failure", receipt_url="")

        _auto_pay_after_register(self.task_id, self.account, {"proxy_url": "http://127.0.0.1:7897"})

        self.assertEqual(mock_run_payment.call_args.kwargs["proxy_url"], "http://127.0.0.1:7897")

    @patch("platforms.chatgpt.payment_auto.subprocess.Popen")
    def test_08_auto_pay_rejects_placeholder_captcha_key_before_subprocess(self, mock_popen):
        config_store.set_many({
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_captcha_api_url": "https://api.yescaptcha.com",
            "payment_captcha_key": "dummy-captcha-key",
        })

        with self.assertRaisesRegex(PaymentError, "payment_captcha_key"):
            run_payment_from_config_store(plan_name="chatgptplusplan", access_token="fake_access_token")

        mock_popen.assert_not_called()

    @patch("api.tasks._run_register")
    def test_09_register_task_preflights_placeholder_auto_pay_config(self, mock_run_register):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_captcha_api_url": "https://api.yescaptcha.com",
            "payment_captcha_key": "dummy-captcha-key",
        })
        req = RegisterTaskRequest(platform="chatgpt", count=1)

        with self.assertRaises(HTTPException) as ctx:
            create_register_task(req, BackgroundTasks())

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("payment_captcha_key", str(ctx.exception.detail))
        mock_run_register.assert_not_called()

    @patch("platforms.chatgpt.payment_auto.requests.post")
    @patch("api.tasks._run_register")
    def test_10_register_task_preflights_remote_rejected_captcha_key(self, mock_run_register, mock_post):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_captcha_api_url": "https://api.yescaptcha.com",
            "payment_captcha_key": "bad-real-looking-key",
            "payment_captcha_validate_online": "1",
        })
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "errorId": 1,
            "errorCode": "ERROR_KEY_DOES_NOT_EXIST",
            "errorDescription": "key does not exist",
        }
        mock_post.return_value = mock_resp
        req = RegisterTaskRequest(platform="chatgpt", count=1)

        with self.assertRaises(HTTPException) as ctx:
            create_register_task(req, BackgroundTasks())

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("ERROR_KEY_DOES_NOT_EXIST", str(ctx.exception.detail))
        mock_run_register.assert_not_called()

    @patch("api.tasks._run_register")
    def test_11_register_task_preflights_missing_payment_python(self, mock_run_register):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_captcha_api_url": "https://api.yescaptcha.com",
            "payment_captcha_key": "real-captcha-key-for-test",
            "payment_captcha_validate_online": "0",
            "payment_python_executable": r"E:\ctf-pay\python.exe",
        })
        req = RegisterTaskRequest(platform="chatgpt", count=1)

        with self.assertRaises(HTTPException) as ctx:
            create_register_task(req, BackgroundTasks())

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("payment_python_executable", str(ctx.exception.detail))
        mock_run_register.assert_not_called()

    @patch("api.tasks._run_register")
    def test_12_register_task_preflights_placeholder_paypal_credentials(self, mock_run_register):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "pay@example.com",
            "payment_paypal_password": "secret",
            "payment_captcha_api_url": "https://api.yescaptcha.com",
            "payment_captcha_key": "real-captcha-key-for-test",
            "payment_captcha_validate_online": "0",
            "payment_python_executable": r"E:\ctf-pay-runtime\Scripts\python.exe",
        })
        req = RegisterTaskRequest(platform="chatgpt", count=1)

        with self.assertRaises(HTTPException) as ctx:
            create_register_task(req, BackgroundTasks())

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("payment_paypal_email", str(ctx.exception.detail))
        mock_run_register.assert_not_called()

    @patch("platforms.chatgpt.payment_auto.subprocess.Popen")
    def test_13_payment_accepts_session_token_without_access_token(self, mock_popen):
        captured_cfg = {}

        def fake_popen(cmd, *args, **kwargs):
            with open(cmd[cmd.index("--config") + 1], "r", encoding="utf-8") as fh:
                captured_cfg.update(json.load(fh))
            mock_process = MagicMock()
            mock_process.stdout.__iter__.return_value = iter([
                'CARD_RESULT_JSON:{"receipt_url": "https://pay.stripe.com/receipts/session", "state": "success"}\n',
            ])
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            return mock_process

        config_store.set_many({
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
        })
        mock_popen.side_effect = fake_popen

        result = run_payment_from_config_store(
            plan_name="chatgptplusplan",
            access_token="",
            session_token="session-token-before-oauth",
            cookie_header="_puid=abc;",
        )

        self.assertTrue(result.success)
        auth_cfg = captured_cfg["fresh_checkout"]["auth"]
        self.assertEqual(auth_cfg["access_token"], "")
        self.assertEqual(auth_cfg["session_token"], "session-token-before-oauth")
        self.assertEqual(auth_cfg["cookie_header"], "_puid=abc;")

    @patch("platforms.chatgpt.payment_auto._terminate_process_tree")
    @patch("platforms.chatgpt.payment_auto.subprocess.Popen")
    def test_13b_payment_fast_fail_terminates_process_tree(self, mock_popen, mock_terminate_tree):
        config_store.set_many({
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
        })
        mock_process = MagicMock()
        mock_process.stdout.__iter__.return_value = iter([
            "[manual_approval] approve 异常: manual_approval approve blocked: result=blocked\n",
        ])
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        with self.assertRaises(PaymentError):
            run_payment_from_config_store(
                plan_name="chatgptplusplan",
                access_token="fake_access_token",
                cookie_header="_puid=abc;",
            )

        mock_terminate_tree.assert_called_with(mock_process)

    @patch("platforms.chatgpt.payment_auto.run_payment_from_config_store")
    def test_14_auto_pay_after_register_skips_if_pre_oauth_already_succeeded(self, mock_run_payment):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
        })
        self.account.extra_json = json.dumps({
            "access_token": "fake_access_token_123",
            "session_token": "fake_session_token_123",
            "auto_pay_plan": "plus",
            "auto_pay_state": "succeeded",
            "auto_pay_flow_order": "before_oauth",
        })

        _auto_pay_after_register(self.task_id, self.account, {})

        mock_run_payment.assert_not_called()

    @patch("platforms.chatgpt.payment_auto.run_payment_from_config_store")
    @patch("api.tasks._log")
    def test_15_pre_oauth_hook_logs_auth_diagnostics_and_skips_missing_auth(self, mock_log, mock_run_payment):
        hook = _make_pre_oauth_auto_pay_hook(self.task_id, {"proxy_url": "http://127.0.0.1:7897"})
        result = MagicMock()
        result.access_token = ""
        result.session_token = ""

        metadata = hook(result, {
            "session_token": "",
            "cookie_header": "oai-did=device-only; _puid=abc",
            "device_id": "device-id",
            "proxy_url": "http://127.0.0.1:7897",
        })

        self.assertEqual(metadata["state"], "skipped_pre_oauth_missing_auth")
        mock_run_payment.assert_not_called()
        logged = "\n".join(str(call.args[1]) for call in mock_log.call_args_list if len(call.args) >= 2)
        self.assertIn("OAuth 前支付凭证检查", logged)
        self.assertIn("access_token=no", logged)
        self.assertIn("session_token=no", logged)
        self.assertIn("cookie_header=yes", logged)

    @patch("platforms.chatgpt.payment_auto.run_payment_from_config_store")
    def test_16_auto_pay_after_register_records_diagnostics_and_idempotency_key(self, mock_run_payment):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_billing_country": "DE",
            "payment_billing_currency": "EUR",
        })
        mock_run_payment.return_value = MagicMock(
            success=True,
            state="succeeded",
            error="",
            receipt_url="https://pay.stripe.com/receipts/diag",
        )

        _auto_pay_after_register(self.task_id, self.account, {"proxy_url": "http://127.0.0.1:7897"})

        extra = json.loads(self.account.extra_json)
        self.assertEqual(extra["auto_pay_state"], "succeeded")
        self.assertEqual(extra["auto_pay_idempotency_key"], _build_payment_idempotency_key(
            account_email=self.account.email,
            plan="plus",
            billing_country="DE",
            billing_currency="EUR",
        ))
        self.assertEqual(extra["auto_pay_geo_diagnostics"]["billing_country"], "DE")
        self.assertEqual(extra["auto_pay_geo_diagnostics"]["billing_currency"], "EUR")
        self.assertEqual(extra["auto_pay_geo_diagnostics"]["browser_locale_hint"], "de-DE")
        self.assertEqual(extra["auto_pay_geo_diagnostics"]["browser_timezone_hint"], "Europe/Berlin")
        self.assertEqual(extra["auto_pay_geo_diagnostics"]["proxy_url"], "http://127.0.0.1:7897")
        mock_run_payment.assert_called_once()

    @patch("platforms.chatgpt.payment_auto.run_payment_from_config_store")
    def test_17_auto_pay_after_register_skips_duplicate_succeeded_idempotency_key(self, mock_run_payment):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_billing_country": "DE",
            "payment_billing_currency": "EUR",
        })
        idem_key = _build_payment_idempotency_key(
            account_email=self.account.email,
            plan="plus",
            billing_country="DE",
            billing_currency="EUR",
        )
        self.account.extra_json = json.dumps({
            "access_token": "fake_access_token_123",
            "session_token": "fake_session_token_123",
            "oai_device_id": "test-device-id-1234",
            "cookie_header": "_puid=xxx;",
            "auto_pay_state": "succeeded",
            "auto_pay_idempotency_key": idem_key,
        })

        _auto_pay_after_register(self.task_id, self.account, {"proxy_url": "http://127.0.0.1:7897"})

        mock_run_payment.assert_not_called()

    @patch("platforms.chatgpt.payment_auto.run_payment_from_config_store")
    def test_18_auto_pay_after_register_records_proxy_geo_consistency_diagnostics(self, mock_run_payment):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_billing_country": "DE",
            "payment_billing_currency": "EUR",
        })
        self.account.extra_json = json.dumps({
            "access_token": "fake_access_token_123",
            "session_token": "fake_session_token_123",
            "oai_device_id": "test-device-id-1234",
            "cookie_header": "_puid=xxx;",
            "proxy_geo_country": "NL",
        })
        mock_run_payment.return_value = MagicMock(
            success=True,
            state="succeeded",
            error="",
            receipt_url="https://pay.stripe.com/receipts/geo",
        )

        _auto_pay_after_register(self.task_id, self.account, {"proxy_url": "http://127.0.0.1:7897"})

        extra = json.loads(self.account.extra_json)
        self.assertEqual(extra["auto_pay_geo_diagnostics"]["proxy_geo_country"], "NL")
        self.assertFalse(extra["auto_pay_geo_diagnostics"]["proxy_geo_matches_billing_country"])
        self.assertEqual(extra["auto_pay_geo_diagnostics"]["proxy_geo_consistency"], "mismatch")

    @patch("platforms.chatgpt.payment_auto.run_payment_from_config_store")
    def test_19_auto_pay_after_register_passes_proxy_geo_country_to_payment(self, mock_run_payment):
        config_store.set_many({
            "payment_auto_plan": "plus",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
        })
        self.account.extra_json = json.dumps({
            "access_token": "fake_access_token_123",
            "session_token": "fake_session_token_123",
            "oai_device_id": "test-device-id-1234",
            "cookie_header": "_puid=xxx;",
            "proxy_geo_country": "JP",
        })
        mock_run_payment.return_value = MagicMock(
            success=True,
            state="succeeded",
            error="",
            receipt_url="https://pay.stripe.com/receipts/jp",
        )

        _auto_pay_after_register(self.task_id, self.account, {"proxy_url": "http://127.0.0.1:7897"})

        self.assertEqual(mock_run_payment.call_args.kwargs["proxy_geo_country"], "JP")

    @patch("platforms.chatgpt.payment_auto.subprocess.Popen")
    def test_20_paypal_uses_japan_proxy_geo_for_billing_and_browser_locale(self, mock_popen):
        captured_cfg = {}

        def fake_popen(cmd, *args, **kwargs):
            with open(cmd[cmd.index("--config") + 1], "r", encoding="utf-8") as fh:
                captured_cfg.update(json.load(fh))
            mock_process = MagicMock()
            mock_process.stdout.__iter__.return_value = iter([
                'CARD_RESULT_JSON:{"receipt_url": "https://pay.stripe.com/receipts/paypal-jp", "state": "success"}\n',
            ])
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            return mock_process

        config_store.set_many({
            "payment_provider": "",
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_billing_country": "US",
            "payment_billing_currency": "USD",
            "payment_promo_proxy_url": "",
            "payment_promo_proxy_geo": "",
        })
        mock_popen.side_effect = fake_popen

        result = run_payment_from_config_store(
            plan_name="chatgptplusplan",
            access_token="fake_access_token",
            proxy_url="http://127.0.0.1:7897",
            proxy_geo_country="JP",
        )

        self.assertTrue(result.success)
        # JP proxy geo is treated as the effective checkout geography for the promo path.
        self.assertEqual(captured_cfg["billing"]["country"], "JP")
        self.assertEqual(captured_cfg["fresh_checkout"]["plan"]["billing_country"], "JP")
        self.assertEqual(captured_cfg["locale"], "JP")

    @patch("platforms.chatgpt.payment_auto.subprocess.Popen")
    def test_20b_paypal_promo_proxy_aligns_japan_billing_for_free_trial(self, mock_popen):
        captured_cfg = {}

        def fake_popen(cmd, *args, **kwargs):
            with open(cmd[cmd.index("--config") + 1], "r", encoding="utf-8") as fh:
                captured_cfg.update(json.load(fh))
            mock_process = MagicMock()
            mock_process.stdout.__iter__.return_value = iter([
                'CARD_RESULT_JSON:{"receipt_url": "https://pay.stripe.com/receipts/paypal-jp", "state": "success"}\n',
            ])
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            return mock_process

        config_store.set_many({
            "payment_method": "paypal",
            "payment_paypal_email": "payer@validmail.local",
            "payment_paypal_password": "valid-paypal-password",
            "payment_billing_country": "US",
            "payment_billing_currency": "USD",
            "payment_promo_proxy_url": "http://127.0.0.1:7897",
            "payment_promo_proxy_geo": "JP",
        })
        mock_popen.side_effect = fake_popen

        result = run_payment_from_config_store(
            plan_name="chatgptplusplan",
            access_token="fake_access_token",
            proxy_url="http://127.0.0.1:7890",
            proxy_geo_country="US",
        )

        self.assertTrue(result.success)
        self.assertEqual(captured_cfg["billing"]["country"], "JP")
        self.assertEqual(captured_cfg["billing"]["currency"], "JPY")
        self.assertEqual(captured_cfg["fresh_checkout"]["plan"]["billing_country"], "JP")
        self.assertEqual(captured_cfg["locale"], "JP")

    def test_21_build_cfg_enables_vlm_external_solver(self):
        cfg = _build_card_cfg(
            plan_name="chatgptplusplan",
            access_token="tk", session_token="st", cookie_header="ck", device_id="id",
            card_number="", card_exp_month="", card_exp_year="", card_cvc="",
            billing_name="John", billing_country="ID", billing_currency="idr",
            billing_address="Jl. Sudirman", billing_city="Jakarta", billing_state="DKI", billing_zip="10110",
            team_workspace_name="", team_seat_quantity=5, captcha_api_url="", captcha_key="",
            gopay_phone="81234567", gopay_pin="123456",
            vlm_base_url="http://127.0.0.1:8080",
            vlm_api_key="sk-test",
            vlm_model="gpt-5.5",
            vlm_timeout_s="60",
        )

        solver = cfg["browser_challenge"]["external_solver"]
        self.assertTrue(solver["enabled"])
        self.assertTrue(solver["headed"])
        self.assertEqual(solver["timeout_s"], 90)
        self.assertEqual(solver["vlm"]["base_url"], "http://127.0.0.1:8080")
        self.assertEqual(solver["vlm"]["api_key"], "sk-test")
        self.assertEqual(solver["vlm"]["model"], "gpt-5.5")
        self.assertEqual(solver["vlm"]["timeout_s"], 60)

    def test_21b_build_cfg_passes_browser_locale_profile_to_external_solver(self):
        cfg = _build_card_cfg(
            plan_name="chatgptplusplan",
            access_token="tk", session_token="st", cookie_header="ck", device_id="id",
            card_number="", card_exp_month="", card_exp_year="", card_cvc="",
            billing_name="Taro", billing_country="JP", billing_currency="JPY",
            billing_address="1-1 Marunouchi", billing_city="Tokyo", billing_state="Tokyo", billing_zip="100-0005",
            team_workspace_name="", team_seat_quantity=5, captcha_api_url="", captcha_key="",
            paypal_email="payer@example.com", paypal_password="secret",
            browser_locale_country="JP",
            vlm_base_url="http://127.0.0.1:8080",
            vlm_api_key="sk-test",
            vlm_model="gpt-5.5",
        )

        challenge = cfg["browser_challenge"]
        self.assertEqual(challenge["browser_locale"], "ja-JP")
        self.assertEqual(challenge["browser_timezone"], "Asia/Tokyo")
        self.assertEqual(challenge["accept_language"], "ja-JP,ja;q=0.9")

    @patch("platforms.chatgpt.payment_auto.subprocess.run")
    @patch("platforms.chatgpt.payment_auto.os.path.isfile")
    def test_22_preflight_rejects_missing_vlm_solver_modules(self, mock_isfile, mock_run):
        mock_isfile.return_value = True
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '{"missing": ["cv2"]}\n'
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        config_store.set_many({
            "payment_method": "gopay",
            "payment_gopay_phone": "81234567",
            "payment_gopay_pin": "123456",
            "payment_captcha_api_url": "https://api.yescaptcha.com",
            "payment_captcha_key": "real-captcha-key-for-test",
            "payment_captcha_validate_online": "0",
            "payment_python_executable": r"E:\ctf-pay-runtime\Scripts\python.exe",
            "payment_vlm_base_url": "http://127.0.0.1:8080",
            "payment_vlm_api_key": "sk-test",
            "payment_vlm_model": "gpt-5.5",
        })

        with self.assertRaisesRegex(PaymentError, "cv2"):
            run_payment_from_config_store(plan_name="chatgptplusplan", access_token="fake_access_token")

        mock_run.assert_called_once()

    @patch("platforms.chatgpt.payment_auto.subprocess.Popen")
    @patch("platforms.chatgpt.payment_auto.subprocess.run")
    @patch("platforms.chatgpt.payment_auto.os.path.isfile")
    def test_23_vlm_config_is_written_to_card_py_config_and_env(self, mock_isfile, mock_run, mock_popen):
        captured_cfg = {}
        captured_env = {}
        mock_isfile.return_value = True
        mock_runtime = MagicMock()
        mock_runtime.returncode = 0
        mock_runtime.stdout = '{"missing": []}\n'
        mock_runtime.stderr = ""
        mock_run.return_value = mock_runtime

        def fake_popen(cmd, *args, **kwargs):
            with open(cmd[cmd.index("--config") + 1], "r", encoding="utf-8") as fh:
                captured_cfg.update(json.load(fh))
            captured_env.update(kwargs.get("env") or {})
            mock_process = MagicMock()
            mock_process.stdout.__iter__.return_value = iter([
                'CARD_RESULT_JSON:{"receipt_url": "https://pay.stripe.com/receipts/vlm", "state": "success"}\n',
            ])
            mock_process.stderr.read.return_value = ""
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            return mock_process

        mock_popen.side_effect = fake_popen
        config_store.set_many({
            "payment_method": "gopay",
            "payment_gopay_phone": "81234567",
            "payment_gopay_pin": "123456",
            "payment_captcha_api_url": "https://api.yescaptcha.com",
            "payment_captcha_key": "real-captcha-key-for-test",
            "payment_captcha_validate_online": "0",
            "payment_python_executable": r"E:\ctf-pay-runtime\Scripts\python.exe",
            "payment_vlm_base_url": "http://127.0.0.1:8080",
            "payment_vlm_api_key": "sk-test",
            "payment_vlm_model": "gpt-5.5",
            "payment_vlm_timeout_s": "60",
        })

        result = run_payment_from_config_store(plan_name="chatgptplusplan", access_token="fake_access_token")

        self.assertTrue(result.success)
        solver = captured_cfg["browser_challenge"]["external_solver"]
        self.assertTrue(solver["enabled"])
        self.assertEqual(solver["vlm"]["base_url"], "http://127.0.0.1:8080")
        self.assertEqual(solver["vlm"]["model"], "gpt-5.5")
        self.assertEqual(captured_env["CTF_VLM_BASE_URL"], "http://127.0.0.1:8080")
        self.assertEqual(captured_env["CTF_VLM_MODEL"], "gpt-5.5")


if __name__ == "__main__":
    unittest.main(verbosity=2)
