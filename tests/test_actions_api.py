from api.actions import _apply_action_result
from core.db import AccountModel


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def test_apply_action_result_persists_chatgpt_cashier_url_and_description():
    account = AccountModel(
        platform="chatgpt",
        email="demo@example.com",
        password="secret",
        extra_json="{}",
    )
    session = _FakeSession()
    url = "https://chatgpt.com/checkout/openai_llc/cs_test_123"
    result = {
        "ok": True,
        "data": {
            "url": url,
            "cashier_url": url,
            "plan": "plus",
            "country": "DE",
            "description": "ChatGPT Plus payment link",
        },
    }

    _apply_action_result("chatgpt", "payment_link", account, result, session)

    extra = account.get_extra()
    assert account.cashier_url == url
    assert extra["cashier_url"] == url
    assert extra["payment_link"]["url"] == url
    assert extra["payment_link"]["plan"] == "plus"
    assert extra["payment_link"]["country"] == "DE"
    assert extra["payment_link"]["description"] == "ChatGPT Plus payment link"
    assert session.added


def test_apply_action_result_persists_sub2api_sync_patch():
    account = AccountModel(
        platform="chatgpt",
        email="demo@example.com",
        password="secret",
        extra_json="{}",
    )
    session = _FakeSession()
    result = {
        "ok": True,
        "data": {
            "message": "Sub2API sync complete",
            "sync": {
                "ok": True,
                "uploaded": True,
                "remote_state": "exists",
                "remote_account_id": 12,
            },
        },
        "account_extra_patch": {
            "sync_statuses": {
                "sub2api": {
                    "ok": True,
                    "uploaded": True,
                    "remote_state": "exists",
                    "remote_account_id": 12,
                }
            }
        },
    }

    _apply_action_result("chatgpt", "sync_sub2api_status", account, result, session)

    extra = account.get_extra()
    assert extra["sync_statuses"]["sub2api"]["remote_state"] == "exists"
    assert extra["sync_statuses"]["sub2api"]["remote_account_id"] == 12
    assert session.added


def test_apply_action_result_clears_stale_sub2api_remote_id_when_skipped():
    account = AccountModel(
        platform="chatgpt",
        email="demo@example.com",
        password="secret",
        extra_json=(
            '{"sync_statuses":{"sub2api":{"uploaded":true,'
            '"uploaded_at":"2026-01-01T00:00:00+00:00","remote_account_id":12}}}'
        ),
    )
    session = _FakeSession()
    result = {
        "ok": False,
        "account_extra_patch": {
            "sync_statuses": {
                "sub2api": {
                    "ok": False,
                    "uploaded": False,
                    "skipped": True,
                    "remote_state": "missing_oauth_credentials",
                    "message": "missing refresh_token",
                }
            }
        },
    }

    _apply_action_result("chatgpt", "sync_sub2api_status", account, result, session)

    state = account.get_extra()["sync_statuses"]["sub2api"]
    assert state["uploaded"] is False
    assert state["remote_state"] == "missing_oauth_credentials"
    assert "remote_account_id" not in state
    assert "uploaded_at" not in state
