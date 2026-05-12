import unittest
from unittest import mock

from core.base_mailbox import LuckMailMailbox, MailboxAccount, create_mailbox
from core.luckmail.http_client import LuckMailHttpClient
from core.luckmail.models import PageResult, PurchaseItem, TokenAliveResult, TokenMailItem, TokenMailList


class LuckMailMailboxTests(unittest.TestCase):
    def _build_mailbox(self):
        mailbox = LuckMailMailbox.__new__(LuckMailMailbox)
        mailbox._client = mock.Mock()
        mailbox._project_code = "openai"
        mailbox._email_type = None
        mailbox._domain = None
        mailbox._order_no = None
        mailbox._token = "tok_demo"
        mailbox._email = "demo@example.com"
        mailbox._log_fn = None
        return mailbox

    @mock.patch("time.sleep", return_value=None)
    def test_wait_for_code_skips_excluded_purchase_code_and_keeps_polling_for_fresh_mail(self, _sleep):
        mailbox = self._build_mailbox()
        mailbox.get_current_ids = mock.Mock(return_value={"m1"})
        mailbox._client.user.get_token_mails.side_effect = [
            TokenMailList(
                email_address="demo@example.com",
                project="openai",
                mails=[
                    TokenMailItem(message_id="m1", subject="Your OpenAI code is 111111"),
                ],
            ),
            TokenMailList(
                email_address="demo@example.com",
                project="openai",
                mails=[
                    TokenMailItem(message_id="m1", subject="Your OpenAI code is 111111"),
                    TokenMailItem(message_id="m2", subject="Your OpenAI code is 222222"),
                ],
            ),
        ]

        code = mailbox.wait_for_code(
            MailboxAccount(email="demo@example.com", account_id="tok_demo"),
            timeout=5,
            exclude_codes={"111111"},
        )

        self.assertEqual(code, "222222")
        mailbox.get_current_ids.assert_called_once()
        self.assertEqual(mailbox._client.user.get_token_mails.call_count, 2)

    @mock.patch("time.sleep", return_value=None)
    def test_wait_for_code_logs_new_purchase_mail_as_one_short_summary(self, _sleep):
        mailbox = self._build_mailbox()
        logs = []
        mailbox._log_fn = logs.append
        mailbox.get_current_ids = mock.Mock(return_value=set())
        mailbox._client.user.get_token_mails.side_effect = [
            TokenMailList(
                email_address="demo@example.com",
                project="openai",
                mails=[
                    TokenMailItem(
                        message_id="m2",
                        subject="Your OpenAI verification email",
                        body="not ready yet",
                    ),
                ],
            ),
            TokenMailList(
                email_address="demo@example.com",
                project="openai",
                mails=[
                    TokenMailItem(
                        message_id="m2",
                        subject="Your OpenAI verification email",
                        body="Your verification code is 333333",
                    ),
                ],
            ),
        ]

        code = mailbox.wait_for_code(
            MailboxAccount(email="demo@example.com", account_id="tok_demo"),
            timeout=5,
        )

        self.assertEqual(code, "333333")
        joined_logs = "\n".join(logs)
        self.assertIn("新邮件=1", joined_logs)
        self.assertIn("待解析=1", joined_logs)
        self.assertNotIn("新邮件已到达", joined_logs)

    @mock.patch("time.sleep", return_value=None)
    def test_wait_for_code_skips_purchase_mail_after_20_second_parse_timeout(self, _sleep):
        mailbox = self._build_mailbox()
        logs = []
        mailbox._log_fn = logs.append
        mailbox.get_current_ids = mock.Mock(return_value=set())
        mailbox._client.user.get_token_mails.side_effect = [
            TokenMailList(
                email_address="demo@example.com",
                project="openai",
                mails=[
                    TokenMailItem(
                        message_id="m2",
                        subject="Your OpenAI verification email",
                        body="not ready yet",
                    ),
                ],
            ),
            TokenMailList(
                email_address="demo@example.com",
                project="openai",
                mails=[
                    TokenMailItem(
                        message_id="m2",
                        subject="Your OpenAI verification email",
                        body="still not ready",
                    ),
                ],
            ),
            TokenMailList(
                email_address="demo@example.com",
                project="openai",
                mails=[
                    TokenMailItem(
                        message_id="m2",
                        subject="Your OpenAI verification email",
                        body="still not ready",
                    ),
                    TokenMailItem(
                        message_id="m3",
                        subject="Your OpenAI verification email",
                        body="Your verification code is 444444",
                    ),
                ],
            ),
        ]

        with mock.patch(
            "core.base_mailbox.time.monotonic",
            side_effect=[100, 100, 100, 100, 110, 110, 110, 121, 121, 121, 131],
        ):
            code = mailbox.wait_for_code(
                MailboxAccount(email="demo@example.com", account_id="tok_demo"),
                timeout=30,
            )

        self.assertEqual(code, "444444")
        joined_logs = "\n".join(logs)
        self.assertIn("m2", joined_logs)
        self.assertIn("20s", joined_logs)

    @mock.patch("time.sleep", return_value=None)
    def test_wait_for_code_times_out_when_no_new_purchase_mail_arrives_for_20_seconds(self, _sleep):
        mailbox = self._build_mailbox()
        logs = []
        mailbox._log_fn = logs.append
        mailbox.get_current_ids = mock.Mock(return_value=set())
        mailbox._client.user.get_token_mails.side_effect = [
            TokenMailList(email_address="demo@example.com", project="openai", mails=[]),
            TokenMailList(email_address="demo@example.com", project="openai", mails=[]),
            TokenMailList(email_address="demo@example.com", project="openai", mails=[]),
        ]

        with mock.patch(
            "core.base_mailbox.time.monotonic",
            side_effect=[100, 100, 100, 100, 110, 110, 110, 121, 121, 121, 131],
        ):
            with self.assertRaises(TimeoutError) as ctx:
                mailbox.wait_for_code(
                    MailboxAccount(email="demo@example.com", account_id="tok_demo"),
                    timeout=30,
                )

        self.assertIn("20s", str(ctx.exception))
        self.assertIn("20s", "\n".join(logs))

    @mock.patch("time.sleep", return_value=None)
    def test_wait_for_code_times_out_after_only_excluded_purchase_mail_arrives(self, _sleep):
        mailbox = self._build_mailbox()
        logs = []
        mailbox._log_fn = logs.append
        mailbox.get_current_ids = mock.Mock(return_value=set())
        old_mail = TokenMailItem(
            message_id="m1",
            subject="Your OpenAI verification code is 111111",
        )
        mailbox._client.user.get_token_mails.side_effect = [
            TokenMailList(email_address="demo@example.com", project="openai", mails=[old_mail]),
            TokenMailList(email_address="demo@example.com", project="openai", mails=[old_mail]),
            TokenMailList(email_address="demo@example.com", project="openai", mails=[old_mail]),
        ]

        with mock.patch(
            "core.base_mailbox.time.monotonic",
            side_effect=[100, 100, 100, 100, 101, 101, 101, 103, 103, 103, 106],
        ):
            with self.assertRaises(TimeoutError) as ctx:
                mailbox.wait_for_code(
                    MailboxAccount(email="demo@example.com", account_id="tok_demo"),
                    timeout=5,
                    exclude_codes={"111111"},
                    no_new_mail_timeout_seconds=2,
                )

        self.assertIn("2s", str(ctx.exception))
        joined_logs = "\n".join(logs)
        self.assertIn("message_id=m1 code=111111", joined_logs)
        self.assertIn("2s", joined_logs)

    @mock.patch("core.luckmail.LuckMailClient")
    def test_factory_passes_proxy_to_luckmail_client(self, client_cls):
        proxy = "http://127.0.0.1:7897"

        create_mailbox(
            "luckmail",
            extra={
                "luckmail_base_url": "https://mails.luckyous.com/",
                "luckmail_api_key": "key",
                "luckmail_project_code": "openai",
            },
            proxy=proxy,
        )

        client_cls.assert_called_once_with(
            base_url="https://mails.luckyous.com/",
            api_key="key",
            proxy_url=proxy,
        )

    def test_luckmail_http_client_uses_explicit_proxy_without_env_proxy(self):
        proxy = "http://127.0.0.1:7897"
        client = LuckMailHttpClient(
            base_url="https://mails.luckyous.com/",
            api_key="key",
            proxy_url=proxy,
        )

        session = client._get_sync_session()

        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies["http"], proxy)
        self.assertEqual(session.proxies["https"], proxy)

    def test_get_email_retries_when_purchased_token_is_dead(self):
        mailbox = self._build_mailbox()
        mailbox._token = None
        mailbox._email = None
        mailbox._client.user.purchase_emails.side_effect = [
            {
                "purchases": [
                    {"id": 101, "email_address": "dead@example.com", "token": "tok_dead"}
                ]
            },
            {
                "purchases": [
                    {"email_address": "good@example.com", "token": "tok_good"}
                ]
            },
        ]
        mailbox._client.user.check_token_alive.side_effect = [
            TokenAliveResult(
                email_address="dead@example.com",
                project="openai",
                alive=False,
                status="failed",
                message="invalid_grant",
            ),
            TokenAliveResult(
                email_address="good@example.com",
                project="openai",
                alive=True,
                status="success",
            ),
        ]
        mailbox._client.user.get_token_mails.return_value = TokenMailList(
            email_address="good@example.com",
            project="openai",
            mails=[],
        )

        account = mailbox.get_email()

        self.assertEqual(account.email, "good@example.com")
        self.assertEqual(account.account_id, "tok_good")
        self.assertEqual(mailbox._client.user.purchase_emails.call_count, 2)
        mailbox._client.user.set_purchase_disabled.assert_called_once_with(101, 1)

    def test_get_email_reuses_existing_active_purchase_before_buying_new_one(self):
        mailbox = self._build_mailbox()
        mailbox._token = None
        mailbox._email = None
        mailbox._client.user.get_purchases.return_value = PageResult(
            list=[
                PurchaseItem(
                    id=88,
                    email_address="reuse@example.com",
                    token="tok_reuse",
                    project_name="OpenAI",
                    price="1.00",
                    user_disabled=0,
                )
            ],
            total=1,
            page=1,
            page_size=100,
        )
        mailbox._client.user.check_token_alive.return_value = TokenAliveResult(
            email_address="reuse@example.com",
            project="openai",
            alive=True,
            status="success",
        )
        mailbox._client.user.get_token_mails.return_value = TokenMailList(
            email_address="reuse@example.com",
            project="openai",
            mails=[],
        )

        account = mailbox.get_email()

        self.assertEqual(account.email, "reuse@example.com")
        self.assertEqual(account.account_id, "tok_reuse")
        self.assertEqual(account.extra["purchase_id"], 88)
        mailbox._client.user.get_purchases.assert_called_once()
        mailbox._client.user.purchase_emails.assert_not_called()

    def test_get_email_retries_when_purchased_mailbox_has_openai_history(self):
        mailbox = self._build_mailbox()
        mailbox._token = None
        mailbox._email = None
        mailbox._client.user.purchase_emails.side_effect = [
            {
                "purchases": [
                    {"email_address": "used@example.com", "token": "tok_used"}
                ]
            },
            {
                "purchases": [
                    {"email_address": "clean@example.com", "token": "tok_clean"}
                ]
            },
        ]
        mailbox._client.user.check_token_alive.return_value = TokenAliveResult(
            email_address="demo@example.com",
            project="openai",
            alive=True,
            status="success",
        )
        mailbox._client.user.get_token_mails.side_effect = [
            TokenMailList(
                email_address="used@example.com",
                project="openai",
                mails=[
                    TokenMailItem(
                        message_id="m1",
                        from_addr="noreply@tm.openai.com",
                        subject="Your ChatGPT code is 123456",
                    ),
                ],
            ),
            TokenMailList(
                email_address="clean@example.com",
                project="openai",
                mails=[],
            ),
        ]

        account = mailbox.get_email()

        self.assertEqual(account.email, "clean@example.com")
        self.assertEqual(account.account_id, "tok_clean")
        self.assertEqual(mailbox._client.user.purchase_emails.call_count, 2)

    def test_get_email_fails_after_repeated_unusable_purchased_mailboxes(self):
        mailbox = self._build_mailbox()
        mailbox._token = None
        mailbox._email = None
        mailbox._client.user.purchase_emails.return_value = {
            "purchases": [
                {"email_address": "dead@example.com", "token": "tok_dead"}
            ]
        }
        mailbox._client.user.check_token_alive.return_value = TokenAliveResult(
            email_address="dead@example.com",
            project="openai",
            alive=False,
            status="failed",
            message="invalid_grant",
        )

        with self.assertRaisesRegex(RuntimeError, "usable LuckMail mailbox"):
            mailbox.get_email()

        self.assertEqual(mailbox._client.user.purchase_emails.call_count, 3)

    def test_update_status_disables_failed_purchase(self):
        mailbox = self._build_mailbox()
        account = MailboxAccount(
            email="failed@example.com",
            account_id="tok_failed",
            extra={"purchase_id": 123},
        )

        mailbox.update_status(account, success=False, error="registration failed")

        mailbox._client.user.set_purchase_disabled.assert_called_once_with(123, 1)

    def test_update_status_keeps_successful_purchase_enabled(self):
        mailbox = self._build_mailbox()
        account = MailboxAccount(
            email="ok@example.com",
            account_id="tok_ok",
            extra={"purchase_id": 456},
        )

        mailbox.update_status(account, success=True, error=None)

        mailbox._client.user.set_purchase_disabled.assert_not_called()


if __name__ == "__main__":
    unittest.main()
