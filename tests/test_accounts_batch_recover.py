from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from api.accounts import router
from core.db import AccountModel, get_session


def _build_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    app = FastAPI()

    def override_get_session():
      with Session(engine) as session:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)
    return app, engine


def test_batch_recover_restores_status_and_clears_failure_flags():
    app, engine = _build_client()
    client = TestClient(app)

    with Session(engine) as session:
        account = AccountModel(
            platform="chatgpt",
            email="failed@example.com",
            password="secret",
            status="invalid",
        )
        account.set_extra(
            {
                "refresh_token": "rt-demo",
                "chatgpt_local": {
                    "auth": {"state": "access_token_invalidated"},
                },
                "sync_statuses": {
                    "sub2api": {
                        "ok": False,
                        "remote_state": "unreachable",
                        "message": "bad",
                    }
                },
            }
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        account_id = int(account.id)

    class _FakeRefreshResult:
        def __init__(self):
            self.success = True
            self.access_token = "new-at"
            self.refresh_token = "new-rt"
            self.error_message = ""

    with patch("api.accounts.TokenRefreshManager") as manager_cls:
        manager_cls.return_value.refresh_account.return_value = _FakeRefreshResult()
        response = client.post("/accounts/batch-recover", json={"ids": [account_id]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["recovered"] == 1
    assert payload["failed"] == 0

    with Session(engine) as session:
        refreshed = session.get(AccountModel, account_id)
        assert refreshed is not None
        assert refreshed.status == "registered"
        assert refreshed.token == "new-at"
        extra = refreshed.get_extra()
        assert extra["refresh_token"] == "new-rt"
        assert "chatgpt_local" not in extra
        assert extra["sync_statuses"]["sub2api"] == {}


def test_list_accounts_supports_success_and_failed_groups():
    app, engine = _build_client()
    client = TestClient(app)

    with Session(engine) as session:
        session.add(
            AccountModel(
                platform="chatgpt",
                email="ok@example.com",
                password="secret",
                status="registered",
            )
        )
        session.add(
            AccountModel(
                platform="chatgpt",
                email="bad@example.com",
                password="secret",
                status="invalid",
            )
        )
        session.add(
            AccountModel(
                platform="chatgpt",
                email="trial@example.com",
                password="secret",
                status="trial",
            )
        )
        session.commit()

    success_response = client.get(
        "/accounts",
        params={"platform": "chatgpt", "status_group": "success"},
    )
    failed_response = client.get(
        "/accounts",
        params={"platform": "chatgpt", "status_group": "failed"},
    )

    assert success_response.status_code == 200
    assert failed_response.status_code == 200

    success_emails = {item["email"] for item in success_response.json()["items"]}
    failed_emails = {item["email"] for item in failed_response.json()["items"]}

    assert success_emails == {"ok@example.com", "trial@example.com"}
    assert failed_emails == {"bad@example.com"}


def test_list_accounts_uses_database_pagination_for_plain_filters():
    app, engine = _build_client()
    client = TestClient(app)

    with Session(engine) as session:
      for index in range(30):
          account = AccountModel(
              platform="chatgpt",
              email=f"user-{index:02d}@example.com",
              password="secret",
              status="registered",
              created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index),
          )
          if index < 20:
              account.extra_json = "{"
          session.add(account)
      session.commit()

    response = client.get(
        "/accounts",
        params={"platform": "chatgpt", "page": 1, "page_size": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 30
    assert len(payload["items"]) == 5
    assert {item["email"] for item in payload["items"]} == {
        "user-29@example.com",
        "user-28@example.com",
        "user-27@example.com",
        "user-26@example.com",
        "user-25@example.com",
    }


def test_list_accounts_summary_omits_heavy_credentials():
    app, engine = _build_client()
    client = TestClient(app)

    with Session(engine) as session:
        account = AccountModel(
            platform="chatgpt",
            email="summary@example.com",
            password="secret",
            status="registered",
            token="access-token",
            extra_json='{"refresh_token":"refresh-token","auto_pay_state":"failed","sync_statuses":{"sub2api":{"state":"ok"}}}',
        )
        session.add(account)
        session.commit()

    response = client.get(
        "/accounts",
        params={"platform": "chatgpt", "summary": "1"},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "token" not in item
    assert "extra_json" not in item
    assert item["has_refresh_token"] is True
    assert item["auto_pay_state"] == "failed"
    assert item["sub2api_sync"] == {"state": "ok"}


def test_list_accounts_treats_probe_invalidated_as_failed_even_without_status_invalid():
    app, engine = _build_client()
    client = TestClient(app)

    with Session(engine) as session:
        account = AccountModel(
            platform="chatgpt",
            email="derived-failed@example.com",
            password="secret",
            status="registered",
        )
        account.set_extra(
            {
                "chatgpt_local": {
                    "auth": {"state": "access_token_invalidated"},
                }
            }
        )
        session.add(account)
        session.commit()

    failed_response = client.get(
        "/accounts",
        params={"platform": "chatgpt", "status": "invalid"},
    )

    assert failed_response.status_code == 200
    failed_emails = {item["email"] for item in failed_response.json()["items"]}
    assert "derived-failed@example.com" in failed_emails


def test_batch_recover_uses_token_refresh_and_keeps_failed_accounts_invalid():
    app, engine = _build_client()
    client = TestClient(app)

    with Session(engine) as session:
        ok_account = AccountModel(
            platform="chatgpt",
            email="recoverable@example.com",
            password="secret",
            status="invalid",
        )
        ok_account.set_extra(
            {
                "refresh_token": "rt-ok",
                "chatgpt_local": {"auth": {"state": "access_token_invalidated"}},
                "sync_statuses": {"sub2api": {"ok": False, "remote_state": "unreachable"}},
            }
        )
        bad_account = AccountModel(
            platform="chatgpt",
            email="still-bad@example.com",
            password="secret",
            status="invalid",
        )
        bad_account.set_extra(
            {
                "chatgpt_local": {"auth": {"state": "access_token_invalidated"}},
            }
        )
        session.add(ok_account)
        session.add(bad_account)
        session.commit()
        session.refresh(ok_account)
        session.refresh(bad_account)
        ok_id = int(ok_account.id)
        bad_id = int(bad_account.id)

    class _FakeRefreshResult:
        def __init__(self, success: bool, access_token: str = "", refresh_token: str = "", error_message: str = ""):
            self.success = success
            self.access_token = access_token
            self.refresh_token = refresh_token
            self.error_message = error_message

    def _fake_refresh_account(account):
        if getattr(account, "email", "") == "recoverable@example.com":
            return _FakeRefreshResult(True, access_token="new-at", refresh_token="new-rt")
        return _FakeRefreshResult(False, error_message="no refresh path")

    with patch("api.accounts.TokenRefreshManager") as manager_cls:
        manager_cls.return_value.refresh_account.side_effect = _fake_refresh_account
        response = client.post("/accounts/batch-recover", json={"ids": [ok_id, bad_id]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["recovered"] == 1
    assert payload["failed"] == 1
    assert payload["items"][0]["ok"] is True
    assert payload["items"][1]["ok"] is False

    with Session(engine) as session:
        refreshed_ok = session.get(AccountModel, ok_id)
        refreshed_bad = session.get(AccountModel, bad_id)
        assert refreshed_ok is not None
        assert refreshed_ok.status == "registered"
        assert refreshed_ok.token == "new-at"
        ok_extra = refreshed_ok.get_extra()
        assert ok_extra["refresh_token"] == "new-rt"
        assert "chatgpt_local" not in ok_extra
        assert ok_extra["sync_statuses"]["sub2api"] == {}

        assert refreshed_bad is not None
        assert refreshed_bad.status == "invalid"
