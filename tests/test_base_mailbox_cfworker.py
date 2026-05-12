import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from core.base_mailbox import MailboxAccount, create_mailbox


class CFWorkerMailboxTests(unittest.TestCase):
    def _build_mailbox(self):
        return create_mailbox(
            "cfworker",
            extra={
                "cfworker_api_url": "https://example.invalid",
                "cfworker_admin_token": "admin-token",
                "cfworker_domain": "mail.example",
            },
        )

    @patch("requests.request")
    def test_get_email_issues_single_request_via_factory_mailbox(self, mock_request):
        mock_request.return_value.status_code = 200
        mock_request.return_value.text = '{"email":"user@mail.example","token":"token-123"}'
        mock_request.return_value.json.return_value = {
            "email": "user@mail.example",
            "token": "token-123",
        }

        mailbox = self._build_mailbox()

        account = mailbox.get_email()

        self.assertEqual(account.email, "user@mail.example")
        self.assertEqual(account.account_id, "token-123")
        mock_request.assert_called_once_with(
            "POST",
            "https://example.invalid/admin/new_address",
            params=None,
            json={"enablePrefix": True, "name": unittest.mock.ANY, "domain": "mail.example"},
            headers={
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "x-admin-auth": "admin-token",
            },
            proxies=None,
            timeout=15,
        )

    @patch("requests.request")
    def test_get_current_ids_issues_single_request_via_factory_mailbox(self, mock_request):
        mock_request.return_value.status_code = 200
        mock_request.return_value.text = '{"results":[{"id":101},{"id":202}]}'
        mock_request.return_value.json.return_value = {
            "results": [
                {"id": 101},
                {"id": 202},
            ]
        }
        mailbox = self._build_mailbox()
        account = MailboxAccount(email="user@mail.example")

        ids = mailbox.get_current_ids(account)

        self.assertEqual(ids, {"101", "202"})
        mock_request.assert_called_once_with(
            "GET",
            "https://example.invalid/admin/mails",
            params={"limit": 20, "offset": 0, "address": "user@mail.example"},
            json=None,
            headers={
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "x-admin-auth": "admin-token",
            },
            proxies=None,
            timeout=10,
        )

    @patch("requests.request")
    def test_get_email_uses_static_subdomain(self, mock_request):
        mock_request.return_value.status_code = 200
        mock_request.return_value.text = '{"email":"user@mail.sub.example","token":"token-123"}'
        mock_request.return_value.json.return_value = {
            "email": "user@mail.sub.example",
            "token": "token-123",
        }

        mailbox = create_mailbox(
            "cfworker",
            extra={
                "cfworker_api_url": "https://example.invalid",
                "cfworker_admin_token": "admin-token",
                "cfworker_domain": "sub.example",
                "cfworker_subdomain": "mail",
            },
        )

        mailbox.get_email()

        self.assertEqual(
            mock_request.call_args.kwargs["json"]["domain"],
            "mail.sub.example",
        )

    @patch("requests.request")
    def test_get_email_uses_random_subdomain(self, mock_request):
        mock_request.return_value.status_code = 200
        mock_request.return_value.text = '{"email":"user@rand.sub.example","token":"token-123"}'
        mock_request.return_value.json.return_value = {
            "email": "user@rand.sub.example",
            "token": "token-123",
        }

        mailbox = create_mailbox(
            "cfworker",
            extra={
                "cfworker_api_url": "https://example.invalid",
                "cfworker_admin_token": "admin-token",
                "cfworker_domain": "*.sub.example",
                "cfworker_subdomain": "mail",
                "cfworker_random_subdomain": True,
            },
        )

        with patch.object(type(mailbox), "_generate_subdomain_label", return_value="rand"):
            mailbox.get_email()

        self.assertEqual(
            mock_request.call_args.kwargs["json"]["domain"],
            "rand.mail.sub.example",
        )

    @patch("requests.request")
    def test_get_email_does_not_use_random_subdomain_without_explicit_wildcard_domain(self, mock_request):
        mock_request.return_value.status_code = 200
        mock_request.return_value.text = '{"email":"user@mail.sub.example","token":"token-123"}'
        mock_request.return_value.json.return_value = {
            "email": "user@mail.sub.example",
            "token": "token-123",
        }

        mailbox = create_mailbox(
            "cfworker",
            extra={
                "cfworker_api_url": "https://example.invalid",
                "cfworker_admin_token": "admin-token",
                "cfworker_domain": "sub.example",
                "cfworker_subdomain": "mail",
                "cfworker_random_subdomain": True,
            },
        )

        with patch.object(type(mailbox), "_generate_subdomain_label", return_value="rand"):
            mailbox.get_email()

        self.assertEqual(
            mock_request.call_args.kwargs["json"]["domain"],
            "mail.sub.example",
        )

    def test_wait_for_code_accepts_second_otp_with_small_pre_cutoff_skew(self):
        mailbox = self._build_mailbox()
        account = MailboxAccount(
            email="user@mail.example",
            account_id="token-123",
        )
        otp_sent_at = datetime(
            2026, 5, 5, 15, 38, 49, tzinfo=timezone.utc
        ).timestamp()
        mails = [
            {
                "id": 2,
                "created_at": "2026-05-05 15:38:32",
                "subject": "Your OpenAI code is 037393",
                "raw": "Your OpenAI code is 037393",
            },
            {
                "id": 1,
                "created_at": "2026-05-05 15:37:34",
                "subject": "Your OpenAI code is 246254",
                "raw": "Your OpenAI code is 246254",
            },
        ]

        with patch.object(type(mailbox), "_get_mails", return_value=mails):
            code = mailbox.wait_for_code(
                account,
                timeout=1,
                otp_sent_at=otp_sent_at,
                exclude_codes={"246254"},
            )

        self.assertEqual(code, "037393")


if __name__ == "__main__":
    unittest.main()
