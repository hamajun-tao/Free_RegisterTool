import hashlib
import unittest
from unittest.mock import patch

from core.base_mailbox import Mail2925WebClient, MailboxAccount, create_mailbox


class Mail2925MailboxTests(unittest.TestCase):
    def _build_mailbox(self, **extra):
        config = {
            "mail2925_login_name": "main",
            "mail2925_password": "secret",
            "mail2925_alias_mode": "plus",
            "mail2925_domain": "2925.com",
        }
        config.update(extra)
        mailbox = create_mailbox("mail2925", extra=config)
        mailbox.web_client = _FakeMail2925WebClient()
        mailbox_logs = []
        mailbox._log_fn = mailbox_logs.append
        mailbox._test_logs = mailbox_logs
        return mailbox

    def test_get_email_generates_plus_alias(self):
        mailbox = self._build_mailbox()

        with patch.object(type(mailbox), "_random_suffix", return_value="abc123"):
            account = mailbox.get_email()

        self.assertEqual(account.email, "main+abc123@2925.com")
        self.assertEqual(account.account_id, "main+abc123@2925.com")
        self.assertEqual(account.extra["provider"], "mail2925")
        self.assertEqual(account.extra["base_email"], "main@2925.com")
        self.assertEqual(account.extra["login_name"], "main")

    def test_create_mailbox_defaults_to_main_alias_mode_when_missing(self):
        mailbox = create_mailbox(
            "mail2925",
            extra={
                "mail2925_login_name": "main",
                "mail2925_password": "secret",
                "mail2925_domain": "2925.com",
            },
        )

        account = mailbox.get_email()

        self.assertEqual(account.email, "main@2925.com")
        self.assertEqual(account.extra["alias_mode"], "main")

    def test_get_current_ids_reads_message_ids_from_web_client(self):
        mailbox = self._build_mailbox(mail2925_alias_mode="main")
        mailbox.web_client.list_responses = [
            {
                "list": [
                    {"messageId": "10"},
                    {"messageId": "11"},
                    {"messageId": "12"},
                ]
            }
        ]

        ids = mailbox.get_current_ids(MailboxAccount(email="main@2925.com"))

        self.assertEqual(ids, {"10", "11", "12"})
        self.assertEqual(
            mailbox.web_client.list_calls,
            [{"folder": "Inbox", "filter_type": 0, "page_index": 1, "page_count": 25}],
        )

    def test_get_current_ids_skips_snapshot_for_plus_alias(self):
        mailbox = self._build_mailbox()
        account = MailboxAccount(
            email="main+abc123@2925.com",
            extra={"provider": "mail2925", "alias_mode": "plus"},
        )

        ids = mailbox.get_current_ids(account)

        self.assertEqual(ids, set())
        self.assertEqual(mailbox.web_client.list_calls, [])

    def test_get_current_ids_falls_back_to_uppercase_inbox_folder(self):
        mailbox = self._build_mailbox(mail2925_alias_mode="main")
        mailbox.web_client.list_responses = [
            {"list": [], "totalCount": 0},
            {"list": [{"messageId": "70"}, {"messageId": "71"}], "totalCount": 2},
        ]

        ids = mailbox.get_current_ids(MailboxAccount(email="main@2925.com"))

        self.assertEqual(ids, {"70", "71"})
        self.assertEqual(
            mailbox.web_client.list_calls,
            [
                {"folder": "Inbox", "filter_type": 0, "page_index": 1, "page_count": 25},
                {"folder": "INBOX", "filter_type": 0, "page_index": 1, "page_count": 25},
            ],
        )

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_filters_alias_skips_seen_and_excluded_codes(self, _sleep):
        mailbox = self._build_mailbox()
        mailbox.web_client.list_responses = [
            {
                "list": [
                    {
                        "messageId": "1",
                        "folder": "Inbox",
                        "toAddress": ["main+abc123@2925.com"],
                        "subject": "old",
                        "bodyContent": "verification code 111111",
                    },
                    {
                        "messageId": "2",
                        "folder": "Inbox",
                        "toAddress": ["main+other@2925.com"],
                        "subject": "other",
                        "bodyContent": "verification code 333333",
                    },
                    {
                        "messageId": "3",
                        "folder": "Inbox",
                        "toAddress": ["main+abc123@2925.com"],
                        "subject": "OpenAI code",
                        "bodyContent": "Your temporary ChatGPT verification code",
                    },
                ]
            }
        ]
        mailbox.web_client.message_details = {
            "3": {
                "mailSubject": "OpenAI code",
                "mailTo": [{"emailAddress": "main+abc123@2925.com"}],
                "bodyHtmlText": "<div>Your verification code is <b>222222</b></div>",
                "bodyText": "",
                "folder": "Inbox",
            }
        }

        code = mailbox.wait_for_code(
            MailboxAccount(email="main+abc123@2925.com"),
            timeout=3,
            before_ids={"1"},
            exclude_codes={"333333"},
        )

        self.assertEqual(code, "222222")
        self.assertEqual(mailbox.web_client.get_calls, [("3", "Inbox", False)])

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_uses_base_mailbox_when_alias_mode_is_main(self, _sleep):
        mailbox = self._build_mailbox(mail2925_alias_mode="main")
        mailbox.web_client.list_responses = [
            {
                "list": [
                    {
                        "messageId": "9",
                        "folder": "Inbox",
                        "toAddress": ["main@2925.com"],
                        "subject": "Your temporary ChatGPT verification code",
                        "bodyContent": "preview only",
                    }
                ]
            }
        ]
        mailbox.web_client.message_details = {
            "9": {
                "mailSubject": "Your temporary ChatGPT verification code",
                "mailTo": [{"emailAddress": "main@2925.com"}],
                "bodyHtmlText": "",
                "bodyText": "verification code 454545",
                "folder": "Inbox",
            }
        }

        code = mailbox.wait_for_code(
            MailboxAccount(
                email="main@2925.com",
                extra={"provider": "mail2925", "base_email": "main@2925.com"},
            ),
            timeout=3,
        )

        self.assertEqual(code, "454545")

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_fetches_detail_when_preview_does_not_contain_alias(self, _sleep):
        mailbox = self._build_mailbox()
        mailbox.web_client.list_responses = [
            {
                "list": [
                    {
                        "messageId": "15",
                        "folder": "Inbox",
                        "toAddress": [],
                        "subject": "OpenAI code",
                        "bodyContent": "preview without recipient",
                    }
                ]
            }
        ]
        mailbox.web_client.message_details = {
            "15": {
                "mailSubject": "OpenAI code",
                "mailTo": [{"emailAddress": "main+abc123@2925.com"}],
                "bodyHtmlText": "<div>Your verification code is <b>818181</b></div>",
                "bodyText": "",
                "folder": "Inbox",
            }
        }

        code = mailbox.wait_for_code(
            MailboxAccount(
                email="main+abc123@2925.com",
                extra={"provider": "mail2925", "base_email": "main@2925.com", "alias_mode": "plus"},
            ),
            timeout=3,
        )

        self.assertEqual(code, "818181")
        self.assertEqual(mailbox.web_client.get_calls, [("15", "Inbox", False)])

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_plus_alias_does_not_consume_main_mailbox_code(self, _sleep):
        mailbox = self._build_mailbox(mail2925_alias_mode="plus")
        mailbox.web_client.list_responses = [
            {
                "list": [
                    {
                        "messageId": "20",
                        "folder": "Inbox",
                        "toAddress": ["main@2925.com"],
                        "subject": "Old main mailbox code",
                        "bodyContent": "verification code 999999",
                    },
                    {
                        "messageId": "21",
                        "folder": "Inbox",
                        "toAddress": ["main+abc123@2925.com"],
                        "subject": "Alias mailbox code",
                        "bodyContent": "verification code 222222",
                    },
                ]
            }
        ]

        code = mailbox.wait_for_code(
            MailboxAccount(
                email="main+abc123@2925.com",
                extra={
                    "provider": "mail2925",
                    "base_email": "main@2925.com",
                    "alias_mode": "plus",
                },
            ),
            timeout=3,
        )

        self.assertEqual(code, "222222")
        self.assertEqual(mailbox.web_client.get_calls, [("21", "Inbox", False)])

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_summarizes_alias_mismatch_logs(self, _sleep):
        mailbox = self._build_mailbox(mail2925_alias_mode="plus")
        mailbox.web_client.list_responses = [
            {
                "list": [
                    {
                        "messageId": "30",
                        "folder": "Inbox",
                        "toAddress": ["main@2925.com"],
                        "subject": "old-1",
                        "bodyContent": "verification code 111111",
                    },
                    {
                        "messageId": "31",
                        "folder": "Inbox",
                        "toAddress": ["main@2925.com"],
                        "subject": "old-2",
                        "bodyContent": "verification code 222222",
                    },
                    {
                        "messageId": "32",
                        "folder": "Inbox",
                        "toAddress": ["main+abc123@2925.com"],
                        "subject": "new",
                        "bodyContent": "verification code 333333",
                    },
                ]
            }
        ]

        code = mailbox.wait_for_code(
            MailboxAccount(
                email="main+abc123@2925.com",
                extra={
                    "provider": "mail2925",
                    "base_email": "main@2925.com",
                    "alias_mode": "plus",
                },
            ),
            timeout=3,
        )

        self.assertEqual(code, "333333")
        combined_logs = "\n".join(mailbox._test_logs)
        self.assertIn("[2925] skip alias mismatch messages: count=2", combined_logs)
        self.assertNotIn("skip message id=30: alias mismatch", combined_logs)
        self.assertNotIn("skip message id=31: alias mismatch", combined_logs)

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_retries_same_alias_message_when_detail_is_not_ready(self, _sleep):
        mailbox = self._build_mailbox(mail2925_alias_mode="plus")
        message = {
            "messageId": "40",
            "folder": "Inbox",
            "toAddress": ["main+abc123@2925.com"],
            "subject": "Your temporary ChatGPT verification code",
            "bodyContent": "Your temporary ChatGPT verification code",
        }
        mailbox.web_client.list_responses = [
            {"list": [message]},
            {"list": [message]},
        ]
        mailbox.web_client.message_details = {
            "40": [
                {
                    "mailSubject": "Your temporary ChatGPT verification code",
                    "mailTo": [{"emailAddress": "main+abc123@2925.com"}],
                    "bodyHtmlText": "",
                    "bodyText": "",
                    "folder": "Inbox",
                },
                {
                    "mailSubject": "Your temporary ChatGPT verification code",
                    "mailTo": [{"emailAddress": "main+abc123@2925.com"}],
                    "bodyHtmlText": "<div>Enter this temporary verification code to continue: <b>555666</b></div>",
                    "bodyText": "",
                    "folder": "Inbox",
                },
            ]
        }

        code = mailbox.wait_for_code(
            MailboxAccount(
                email="main+abc123@2925.com",
                extra={
                    "provider": "mail2925",
                    "base_email": "main@2925.com",
                    "alias_mode": "plus",
                },
            ),
            timeout=3,
        )

        self.assertEqual(code, "555666")
        self.assertEqual(
            mailbox.web_client.get_calls,
            [("40", "Inbox", False), ("40", "Inbox", False)],
        )

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_continues_when_one_detail_fetch_fails(self, _sleep):
        mailbox = self._build_mailbox(mail2925_alias_mode="plus")
        mailbox.web_client.list_responses = [
            {
                "list": [
                    {
                        "messageId": "50",
                        "folder": "Inbox",
                        "toAddress": ["main+abc123@2925.com"],
                        "subject": "Your temporary ChatGPT verification code",
                        "bodyContent": "Your temporary ChatGPT verification code",
                    },
                    {
                        "messageId": "51",
                        "folder": "Inbox",
                        "toAddress": ["main+abc123@2925.com"],
                        "subject": "Your temporary ChatGPT verification code",
                        "bodyContent": "Your temporary ChatGPT verification code",
                    },
                ]
            }
        ]
        mailbox.web_client.message_details = {
            "50": RuntimeError("detail endpoint temporary failure"),
            "51": {
                "mailSubject": "Your temporary ChatGPT verification code",
                "mailTo": [{"emailAddress": "main+abc123@2925.com"}],
                "bodyHtmlText": "<div>Enter this temporary verification code to continue: <b>777888</b></div>",
                "bodyText": "",
                "folder": "Inbox",
            },
        }

        code = mailbox.wait_for_code(
            MailboxAccount(
                email="main+abc123@2925.com",
                extra={
                    "provider": "mail2925",
                    "base_email": "main@2925.com",
                    "alias_mode": "plus",
                },
            ),
            timeout=3,
        )

        self.assertEqual(code, "777888")
        self.assertEqual(
            mailbox.web_client.get_calls,
            [("50", "Inbox", False), ("51", "Inbox", False)],
        )

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_falls_back_to_uppercase_inbox_folder(self, _sleep):
        mailbox = self._build_mailbox(mail2925_alias_mode="plus")
        mailbox.web_client.list_responses = [
            {"list": [], "totalCount": 0},
            {
                "list": [
                    {
                        "messageId": "60",
                        "folder": "Inbox",
                        "toAddress": ["main+abc123@2925.com"],
                        "subject": "Your temporary ChatGPT verification code",
                        "bodyContent": "verification code 123123",
                    }
                ],
                "totalCount": 1,
            },
        ]

        code = mailbox.wait_for_code(
            MailboxAccount(
                email="main+abc123@2925.com",
                extra={
                    "provider": "mail2925",
                    "base_email": "main@2925.com",
                    "alias_mode": "plus",
                },
            ),
            timeout=3,
        )

        self.assertEqual(code, "123123")
        self.assertEqual(
            mailbox.web_client.list_calls,
            [
                {"folder": "Inbox", "filter_type": 0, "page_index": 1, "page_count": 25},
                {"folder": "INBOX", "filter_type": 0, "page_index": 1, "page_count": 25},
            ],
        )

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_retries_without_proxy_after_proxy_list_failure(self, _sleep):
        mailbox = self._build_mailbox(mail2925_alias_mode="plus")
        mailbox.web_client = _ProxyFallbackMail2925WebClient(
            list_sequences=[
                RuntimeError(
                    "HTTPSConnectionPool(host='mail.2925.com', port=443): Max retries exceeded "
                    "with url: /mailv2/maildata/MailList/mails?Folder=Inbox "
                    "(Caused by ProxyError('Unable to connect to proxy', "
                    "RemoteDisconnected('Remote end closed connection without response')))"
                ),
                {
                    "list": [
                        {
                            "messageId": "70",
                            "folder": "Inbox",
                            "toAddress": ["main+abc123@2925.com"],
                            "subject": "Your temporary ChatGPT verification code",
                            "bodyContent": "verification code 919191",
                        }
                    ],
                    "totalCount": 1,
                },
            ]
        )

        code = mailbox.wait_for_code(
            MailboxAccount(
                email="main+abc123@2925.com",
                extra={
                    "provider": "mail2925",
                    "base_email": "main@2925.com",
                    "alias_mode": "plus",
                },
            ),
            timeout=3,
        )

        self.assertEqual(code, "919191")
        self.assertEqual(mailbox.web_client.proxy, None)
        combined_logs = "\n".join(mailbox._test_logs)
        self.assertIn("[2925] proxy failed, retrying mailbox without proxy", combined_logs)

    @patch("time.sleep", return_value=None)
    def test_wait_for_code_keeps_polling_same_message_after_proxy_detail_failure(self, _sleep):
        mailbox = self._build_mailbox(mail2925_alias_mode="plus")
        mailbox.web_client = _ProxyFallbackMail2925WebClient(
            list_sequences=[
                {
                    "list": [
                        {
                            "messageId": "71",
                            "folder": "Inbox",
                            "toAddress": ["main+abc123@2925.com"],
                            "subject": "Your temporary ChatGPT verification code",
                            "bodyContent": "Your temporary ChatGPT verification code",
                        }
                    ],
                    "totalCount": 1,
                },
                {
                    "list": [
                        {
                            "messageId": "71",
                            "folder": "Inbox",
                            "toAddress": ["main+abc123@2925.com"],
                            "subject": "Your temporary ChatGPT verification code",
                            "bodyContent": "Your temporary ChatGPT verification code",
                        }
                    ],
                    "totalCount": 1,
                },
            ],
            detail_sequences={
                "71": [
                    RuntimeError(
                        "HTTPSConnectionPool(host='mail.2925.com', port=443): Max retries exceeded "
                        "with url: /mailv2/maildata/MailRead/mails/read?MessageID=71 "
                        "(Caused by ProxyError('Unable to connect to proxy', "
                        "RemoteDisconnected('Remote end closed connection without response')))"
                    ),
                    {
                        "mailSubject": "Your temporary ChatGPT verification code",
                        "mailTo": [{"emailAddress": "main+abc123@2925.com"}],
                        "bodyHtmlText": "<div>verification code <b>565656</b></div>",
                        "bodyText": "",
                        "folder": "Inbox",
                    },
                ]
            },
        )

        code = mailbox.wait_for_code(
            MailboxAccount(
                email="main+abc123@2925.com",
                extra={
                    "provider": "mail2925",
                    "base_email": "main@2925.com",
                    "alias_mode": "plus",
                },
            ),
            timeout=3,
        )

        self.assertEqual(code, "565656")
        self.assertEqual(
            mailbox.web_client.get_calls,
            [("71", "Inbox", False), ("71", "Inbox", False)],
        )

    def test_create_mailbox_uses_new_login_name_config(self):
        mailbox = self._build_mailbox(
            mail2925_login_name="3320665692a",
            mail2925_domain="2925.com",
            mail2925_alias_mode="main",
        )

        account = mailbox.get_email()

        self.assertEqual(account.email, "3320665692a@2925.com")
        self.assertEqual(account.extra["base_email"], "3320665692a@2925.com")

    @patch("requests.Session")
    def test_web_client_login_uses_md5_password_and_stores_jwt_cookie(self, mock_session_cls):
        fake_session = _FakeRequestsSession()
        fake_session.post_responses = [
            _FakeResponse(
                {
                    "code": 200,
                    "result": {"success": True, "token": "access-token"},
                },
                cookies={"auc": "cookie-auc"},
            ),
            _FakeResponse({"code": 200, "result": "jwt-token"}),
        ]
        mock_session_cls.return_value = fake_session

        client = Mail2925WebClient(
            login_name="3320665692a",
            password="agou950523.",
            domain="2925.com",
            proxy="http://127.0.0.1:7897",
        )

        session = client._ensure_session()

        self.assertIs(session, fake_session)
        self.assertEqual(len(fake_session.post_calls), 2)
        self.assertEqual(
            fake_session.post_calls[0]["url"],
            "https://mail.2925.com/mailv2/auth/weblogin",
        )
        self.assertEqual(
            fake_session.post_calls[0]["data"]["uname"],
            "3320665692a@2925.com",
        )
        self.assertEqual(
            fake_session.post_calls[0]["data"]["rsapwd"],
            hashlib.md5("agou950523.".encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            fake_session.post_calls[1]["url"],
            "https://mail.2925.com/mailv2/auth/token",
        )
        self.assertEqual(session.headers["Authorization"], "Bearer access-token")
        self.assertIn("deviceUid", session.headers)
        self.assertEqual(session.cookies.get("jwt_token"), "jwt-token")
        self.assertEqual(session.cookies.get("auc"), "cookie-auc")


class _FakeMail2925WebClient:
    def __init__(self):
        self.list_responses = []
        self.message_details = {}
        self.list_calls = []
        self.get_calls = []

    def list_messages(self, folder="Inbox", filter_type=0, page_index=1, page_count=25):
        self.list_calls.append(
            {
                "folder": folder,
                "filter_type": filter_type,
                "page_index": page_index,
                "page_count": page_count,
            }
        )
        if not self.list_responses:
            return {"list": []}
        return self.list_responses.pop(0)

    def get_message(self, message_id, folder_name="Inbox", is_pre=False):
        self.get_calls.append((message_id, folder_name, is_pre))
        detail = self.message_details.get(message_id, {})
        if isinstance(detail, list):
            if not detail:
                return {}
            return detail.pop(0)
        if isinstance(detail, Exception):
            raise detail
        return detail


class _ProxyFallbackMail2925WebClient(_FakeMail2925WebClient):
    def __init__(self, list_sequences=None, detail_sequences=None):
        super().__init__()
        self.list_sequences = list_sequences or []
        self.detail_sequences = detail_sequences or {}
        self.proxy = "http://proxy.local:8080"

    def list_messages(self, folder="Inbox", filter_type=0, page_index=1, page_count=25):
        self.list_calls.append(
            {
                "folder": folder,
                "filter_type": filter_type,
                "page_index": page_index,
                "page_count": page_count,
                "proxy": self.proxy,
            }
        )
        if not self.list_sequences:
            return {"list": []}
        result = self.list_sequences.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def get_message(self, message_id, folder_name="Inbox", is_pre=False):
        self.get_calls.append((message_id, folder_name, is_pre))
        seq = self.detail_sequences.get(message_id, [])
        if seq:
            result = seq.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return super().get_message(message_id, folder_name, is_pre)


class _FakeResponse:
    def __init__(self, payload, cookies=None):
        self._payload = payload
        self.cookies = cookies or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeCookieJar(dict):
    def set(self, name, value, domain=None, path=None):
        self[name] = value

    def update(self, other):
        if other:
            super().update(dict(other))

    def get(self, name, default=None):
        return super().get(name, default)


class _FakeRequestsSession:
    def __init__(self):
        self.proxies = {}
        self.headers = {}
        self.cookies = _FakeCookieJar()
        self.post_responses = []
        self.post_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        if not self.post_responses:
            raise AssertionError("unexpected post call")
        return self.post_responses.pop(0)


if __name__ == "__main__":
    unittest.main()
