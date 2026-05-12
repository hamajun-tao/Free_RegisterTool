"""
Sub2API 上传功能
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Tuple

from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)

DEFAULT_GROUP_IDS = [2]
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
SUB2API_MISSING_OAUTH_CREDENTIALS_MESSAGE = "Sub2API requires real OAuth credentials: missing refresh_token or id_token"


def _get_config_value(key: str) -> str:
    try:
        from core.config_store import config_store

        return str(config_store.get(key, "") or "").strip()
    except Exception:
        return ""


def _parse_group_ids(raw: Any, fallback: list[int] | None = None) -> list[int]:
    candidates: list[Any]
    if isinstance(raw, str):
        candidates = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        candidates = list(raw)
    elif raw is None:
        candidates = []
    else:
        candidates = [raw]

    values: list[int] = []
    for item in candidates:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            values.append(int(text))
        except ValueError:
            continue

    return values or list(fallback or DEFAULT_GROUP_IDS)


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        data = json.loads(decoded)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_auth(payload: dict[str, Any]) -> dict[str, Any]:
    auth_info = payload.get("https://api.openai.com/auth")
    return auth_info if isinstance(auth_info, dict) else {}


def _extract_organization_id(id_token_payload: dict[str, Any]) -> str:
    auth_info = _extract_auth(id_token_payload)
    organization_id = str(auth_info.get("organization_id") or "").strip()
    if organization_id:
        return organization_id

    organizations = auth_info.get("organizations") or []
    if isinstance(organizations, list):
        for item in organizations:
            if isinstance(item, dict):
                organization_id = str(item.get("id") or "").strip()
                if organization_id:
                    return organization_id
    return ""


def _raw_account_token_data(account) -> dict[str, str]:
    return {
        "email": str(getattr(account, "email", "") or "").strip(),
        "access_token": str(getattr(account, "access_token", "") or "").strip(),
        "refresh_token": str(getattr(account, "refresh_token", "") or "").strip(),
        "id_token": str(getattr(account, "id_token", "") or "").strip(),
    }


def _validate_sub2api_oauth_credentials(account) -> tuple[bool, str]:
    token_data = _raw_account_token_data(account)
    if not token_data["access_token"]:
        return False, "Sub2API requires OAuth credentials: missing access_token"
    if not token_data["refresh_token"] or not token_data["id_token"]:
        return False, SUB2API_MISSING_OAUTH_CREDENTIALS_MESSAGE
    return True, ""


def _missing_oauth_sync_result(message: str = SUB2API_MISSING_OAUTH_CREDENTIALS_MESSAGE) -> dict[str, Any]:
    return {
        "ok": False,
        "uploaded": False,
        "skipped": True,
        "remote_state": "missing_oauth_credentials",
        "message": message,
    }


def local_sub2api_account_sync_result(account) -> dict[str, Any] | None:
    credentials_ok, credentials_msg = _validate_sub2api_oauth_credentials(account)
    if credentials_ok:
        return None
    return _missing_oauth_sync_result(credentials_msg)


def _build_sub2api_account_payload(account, group_ids: list[int] | None = None) -> dict[str, Any]:
    token_data = _raw_account_token_data(account)
    access_token = str(token_data.get("access_token") or "").strip()
    refresh_token = str(token_data.get("refresh_token") or "").strip()
    id_token = str(token_data.get("id_token") or "").strip()
    email = str(token_data.get("email") or getattr(account, "email", "") or "").strip()

    access_payload = _decode_jwt_payload(access_token)
    access_auth = _extract_auth(access_payload)
    expires_at = access_payload.get("exp")
    if not isinstance(expires_at, int) or expires_at <= 0:
        expires_at = int(time.time()) + 863999

    # 关键逻辑：Sub2API 依赖 OpenAI OAuth 结构化字段，这里尽量从现有 token 自动补齐。
    organization_id = _extract_organization_id(_decode_jwt_payload(id_token))

    return {
        "name": email,
        "notes": "",
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 863999,
            "expires_at": expires_at,
            "chatgpt_account_id": str(
                access_auth.get("chatgpt_account_id") or token_data.get("account_id") or ""
            ).strip(),
            "chatgpt_user_id": str(access_auth.get("chatgpt_user_id") or "").strip(),
            "organization_id": organization_id,
            "client_id": str(getattr(account, "client_id", "") or DEFAULT_CLIENT_ID).strip() or DEFAULT_CLIENT_ID,
            "id_token": id_token,
        },
        "extra": {"email": email},
        "group_ids": _parse_group_ids(group_ids),
        "concurrency": 10,
        "priority": 1,
        "auto_pause_on_expired": True,
    }


def _resolve_sub2api_config(
    api_url: str | None = None,
    api_key: str | None = None,
) -> tuple[str, str]:
    return (
        str(api_url or _get_config_value("sub2api_api_url")).strip(),
        str(api_key or _get_config_value("sub2api_api_key")).strip(),
    )


def _sub2api_headers(api_url: str, api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{api_url.rstrip('/')}/admin/accounts",
        "x-api-key": api_key,
    }


def _extract_sub2api_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    items = payload.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _match_sub2api_account(items: list[dict[str, Any]], email: str) -> dict[str, Any] | None:
    target = str(email or "").strip().lower()
    if not target:
        return None
    for item in items:
        name = str(item.get("name") or "").strip().lower()
        if name == target:
            return item
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        extra_email = str(extra.get("email") or "").strip().lower()
        if extra_email == target:
            return item
    return None


def query_sub2api_account(
    email: str,
    api_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    api_url, api_key = _resolve_sub2api_config(api_url, api_key)
    if not api_url:
        return {"ok": False, "found": False, "remote_state": "unconfigured", "uploaded": False, "message": "Sub2API API URL 未配置", "account": None}
    if not api_key:
        return {"ok": False, "found": False, "remote_state": "unconfigured", "uploaded": False, "message": "Sub2API API Key 未配置", "account": None}

    url = f"{api_url.rstrip('/')}/api/v1/admin/accounts"
    try:
        response = cffi_requests.get(
            url,
            headers=_sub2api_headers(api_url, api_key),
            params={
                "page": 1,
                "page_size": 20,
                "platform": "openai",
                "search": str(email or "").strip(),
            },
            proxies=None,
            verify=False,
            timeout=30,
            impersonate="chrome136",
        )
        if response.status_code != 200:
            return {
                "ok": False,
                "found": False,
                "remote_state": "unreachable",
                "uploaded": False,
                "message": f"查询失败: HTTP {response.status_code}",
                "account": None,
            }
        payload = response.json()
        items = _extract_sub2api_items(payload)
        matched = _match_sub2api_account(items, email)
        if matched:
            return {
                "ok": True,
                "found": True,
                "remote_state": "exists",
                "uploaded": True,
                "message": "远端已存在",
                "account": matched,
                "remote_account_id": matched.get("id"),
            }
        return {
            "ok": True,
            "found": False,
            "remote_state": "not_found",
            "uploaded": False,
            "message": "远端未发现",
            "account": None,
        }
    except Exception as exc:
        logger.error("Sub2API 查询异常: %s", exc)
        return {
            "ok": False,
            "found": False,
            "remote_state": "unreachable",
            "uploaded": False,
            "message": f"查询异常: {exc}",
            "account": None,
        }


def _create_sub2api_account(
    account,
    api_url: str,
    api_key: str,
    group_ids: list[int] | None = None,
) -> Tuple[bool, str]:
    resolved_group_ids = _parse_group_ids(
        _get_config_value("sub2api_group_ids") if group_ids is None else group_ids
    )
    payload = _build_sub2api_account_payload(account, group_ids=resolved_group_ids)
    url = f"{api_url.rstrip('/')}/api/v1/admin/accounts"
    try:
        response = cffi_requests.post(
            url,
            headers=_sub2api_headers(api_url, api_key),
            json=payload,
            proxies=None,
            verify=False,
            timeout=30,
            impersonate="chrome136",
        )

        if response.status_code in (200, 201):
            return True, "上传成功"

        error_msg = f"上传失败: HTTP {response.status_code}"
        try:
            detail = response.json()
            if isinstance(detail, dict):
                error_msg = str(
                    detail.get("message")
                    or detail.get("msg")
                    or detail.get("error")
                    or error_msg
                )
        except Exception:
            error_msg = f"{error_msg} - {response.text[:200]}"
        return False, error_msg
    except Exception as exc:
        logger.error("Sub2API 上传异常: %s", exc)
        return False, f"上传异常: {exc}"


def upload_to_sub2api(
    account,
    api_url: str | None = None,
    api_key: str | None = None,
    group_ids: list[int] | None = None,
) -> Tuple[bool, str]:
    """上传单个账号到 Sub2API 管理后台。"""
    api_url, api_key = _resolve_sub2api_config(api_url, api_key)

    if not api_url:
        return False, "Sub2API API URL 未配置"
    if not api_key:
        return False, "Sub2API API Key 未配置"

    credentials_ok, credentials_msg = _validate_sub2api_oauth_credentials(account)
    if not credentials_ok:
        return False, credentials_msg

    email = str(getattr(account, "email", "") or "").strip()
    query_result = query_sub2api_account(email, api_url=api_url, api_key=api_key)
    if query_result.get("ok") and query_result.get("found"):
        return True, "远端已存在，跳过上传"
    if query_result.get("remote_state") not in ("not_found",):
        return False, str(query_result.get("message") or "Sub2API 查询失败")

    return _create_sub2api_account(account, api_url=api_url, api_key=api_key, group_ids=group_ids)


def sync_sub2api_account(
    account,
    api_url: str | None = None,
    api_key: str | None = None,
    group_ids: list[int] | None = None,
) -> dict[str, Any]:
    api_url, api_key = _resolve_sub2api_config(api_url, api_key)
    local_result = local_sub2api_account_sync_result(account)
    if local_result is not None:
        return local_result

    email = str(getattr(account, "email", "") or "").strip()
    initial = query_sub2api_account(email, api_url=api_url, api_key=api_key)
    if not initial.get("ok"):
        return {
            "ok": False,
            "uploaded": False,
            "skipped": False,
            "remote_state": initial.get("remote_state") or "unreachable",
            "message": initial.get("message") or "Sub2API 查询失败",
        }
    if initial.get("found"):
        return {
            "ok": True,
            "uploaded": True,
            "skipped": True,
            "remote_state": "exists",
            "message": "远端已存在，跳过上传",
            "remote_account_id": initial.get("remote_account_id"),
        }

    ok, msg = _create_sub2api_account(account, api_url=api_url, api_key=api_key, group_ids=group_ids)
    if not ok:
        return {
            "ok": False,
            "uploaded": False,
            "skipped": False,
            "remote_state": "not_found",
            "message": msg,
        }

    verified = query_sub2api_account(email, api_url=api_url, api_key=api_key)
    if verified.get("ok") and verified.get("found"):
        return {
            "ok": True,
            "uploaded": True,
            "skipped": False,
            "remote_state": "exists",
            "message": "上传成功",
            "remote_account_id": verified.get("remote_account_id"),
        }
    if verified.get("ok"):
        return {
            "ok": False,
            "uploaded": False,
            "skipped": False,
            "remote_state": "not_found",
            "message": "上传成功但远端未确认",
        }
    return {
        "ok": True,
        "uploaded": True,
        "skipped": False,
        "remote_state": "created_unverified",
        "message": f"上传成功，但校验失败: {verified.get('message') or 'unknown'}",
    }
