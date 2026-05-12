from unittest import mock

from platforms.chatgpt.sub2api_upload import (
    _build_sub2api_account_payload,
    local_sub2api_account_sync_result,
    sync_sub2api_account,
    upload_to_sub2api,
)


class _Account:
    def __init__(self, **kwargs):
        self.email = kwargs.get("email", "demo@example.com")
        self.access_token = kwargs.get("access_token", "access-token")
        self.refresh_token = kwargs.get("refresh_token", "")
        self.id_token = kwargs.get("id_token", "")
        self.client_id = kwargs.get("client_id", "client-id")


def test_sub2api_payload_uses_real_id_token_not_cpa_compat_token():
    account = _Account(refresh_token="refresh-token", id_token="")

    payload = _build_sub2api_account_payload(account)

    assert payload["credentials"]["refresh_token"] == "refresh-token"
    assert payload["credentials"]["id_token"] == ""


def test_upload_to_sub2api_skips_access_token_only_account_without_remote_call():
    account = _Account(refresh_token="", id_token="")

    with mock.patch("platforms.chatgpt.sub2api_upload.query_sub2api_account") as query, mock.patch(
        "platforms.chatgpt.sub2api_upload._create_sub2api_account"
    ) as create:
        ok, msg = upload_to_sub2api(account, api_url="http://sub2api.local", api_key="key")

    assert ok is False
    assert "refresh_token" in msg
    query.assert_not_called()
    create.assert_not_called()


def test_sync_sub2api_account_marks_access_token_only_as_skipped():
    account = _Account(refresh_token="", id_token="")

    with mock.patch("platforms.chatgpt.sub2api_upload.query_sub2api_account") as query, mock.patch(
        "platforms.chatgpt.sub2api_upload._create_sub2api_account"
    ) as create:
        result = sync_sub2api_account(account, api_url="http://sub2api.local", api_key="key")

    assert result["ok"] is False
    assert result["uploaded"] is False
    assert result["skipped"] is True
    assert result["remote_state"] == "missing_oauth_credentials"
    assert "refresh_token" in result["message"]
    query.assert_not_called()
    create.assert_not_called()


def test_local_sub2api_sync_result_skips_access_token_only_account():
    account = _Account(refresh_token="", id_token="")

    result = local_sub2api_account_sync_result(account)

    assert result["ok"] is False
    assert result["uploaded"] is False
    assert result["skipped"] is True
    assert result["remote_state"] == "missing_oauth_credentials"
