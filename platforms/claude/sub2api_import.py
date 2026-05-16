"""
Claude (Anthropic) account import to Sub2API.

Supports two modes:
- Mode A (session_key): Exchange a Claude sessionKey cookie via
  sub2api's CookieAuth endpoint to obtain OAuth tokens, then create
  the account in sub2api as platform="anthropic".
- Mode B (tokens): Accept existing OAuth tokens and create the
  sub2api account directly.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Tuple

from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)

DEFAULT_GROUP_IDS = [2]
SESSION_KEY_PREFIX = "sk-ant-sid01-"


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


def _resolve_sub2api_config(
    api_url: str | None = None,
    api_key: str | None = None,
) -> tuple[str, str]:
    return (
        str(api_url or _get_config_value("sub2api_api_url")).strip(),
        str(api_key or _get_config_value("sub2api_api_key")).strip(),
    )


def _sub2api_headers(api_url: str, api_key: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{api_url.rstrip('/')}/admin/accounts",
    }
    if str(api_key or "").startswith("eyJ"):
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["x-api-key"] = api_key
    return headers


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


def _extract_error_message(response) -> str:
    try:
        detail = response.json()
        if isinstance(detail, dict):
            return str(
                detail.get("message")
                or detail.get("msg")
                or detail.get("error")
                or f"HTTP {response.status_code}"
            )
    except Exception:
        pass
    try:
        return response.text[:200]
    except Exception:
        return f"HTTP {response.status_code}"


# ── Session Key Exchange ──────────────────────────────────────────────────────


def _exchange_session_key(
    session_key: str,
    api_url: str,
    api_key: str,
    use_setup_token: bool = False,
) -> dict[str, Any]:
    endpoint = (
        "/api/v1/admin/accounts/setup-token-cookie-auth"
        if use_setup_token
        else "/api/v1/admin/accounts/cookie-auth"
    )
    url = f"{api_url.rstrip('/')}{endpoint}"
    try:
        response = cffi_requests.post(
            url,
            headers=_sub2api_headers(api_url, api_key),
            json={"code": session_key, "proxy_id": None},
            proxies=None,
            verify=False,
            timeout=60,
            impersonate="chrome136",
        )
        if response.status_code in (200, 201):
            data = response.json()
            if isinstance(data, dict):
                return {"ok": True, "tokens": data, "message": "sessionKey 交换成功"}
            return {"ok": False, "tokens": None, "message": f"CookieAuth 返回格式异常: {str(data)[:200]}"}
        return {
            "ok": False,
            "tokens": None,
            "message": f"CookieAuth 失败: HTTP {response.status_code} - {_extract_error_message(response)}",
        }
    except Exception as exc:
        logger.error("Claude sessionKey 交换异常: %s", exc)
        return {"ok": False, "tokens": None, "message": f"sessionKey 交换异常: {exc}"}


# ── Account Payload Builder ────────────────────────────────────────────────────


def _build_claude_account_payload(
    tokens: dict[str, Any],
    group_ids: list[int] | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    email_address = str(tokens.get("email_address") or "").strip()
    return {
        "name": name or email_address or "Claude Account",
        "platform": "anthropic",
        "type": "oauth",
        "credentials": {
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "expires_in": tokens.get("expires_in", 3600),
            "expires_at": tokens.get("expires_at", int(time.time()) + 3600),
            "token_type": tokens.get("token_type", "bearer"),
            "scope": tokens.get("scope", ""),
            "org_uuid": tokens.get("org_uuid", ""),
            "account_uuid": tokens.get("account_uuid", ""),
            "email_address": email_address,
        },
        "extra": {"email": email_address},
        "group_ids": _parse_group_ids(group_ids),
        "concurrency": 1,
        "priority": 1,
        "auto_pause_on_expired": True,
    }


# ── Query ──────────────────────────────────────────────────────────────────────


def query_sub2api_claude_account(
    email: str,
    api_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    api_url, api_key = _resolve_sub2api_config(api_url, api_key)
    if not api_url:
        return {
            "ok": False, "found": False, "remote_state": "unconfigured",
            "message": "Sub2API API URL 未配置",
            "account": None,
        }
    if not api_key:
        return {
            "ok": False, "found": False, "remote_state": "unconfigured",
            "message": "Sub2API API Key 未配置",
            "account": None,
        }

    url = f"{api_url.rstrip('/')}/api/v1/admin/accounts"
    try:
        response = cffi_requests.get(
            url,
            headers=_sub2api_headers(api_url, api_key),
            params={
                "page": 1,
                "page_size": 20,
                "platform": "anthropic",
                "search": str(email or "").strip(),
            },
            proxies=None,
            verify=False,
            timeout=30,
            impersonate="chrome136",
        )
        if response.status_code != 200:
            return {
                "ok": False, "found": False, "remote_state": "unreachable",
                "message": f"查询失败: HTTP {response.status_code}",
                "account": None,
            }
        payload = response.json()
        items = _extract_sub2api_items(payload)
        matched = _match_sub2api_account(items, email)
        if matched:
            return {
                "ok": True, "found": True, "remote_state": "exists",
                "message": "远端已存在",
                "account": matched,
                "remote_account_id": matched.get("id"),
            }
        return {
            "ok": True, "found": False, "remote_state": "not_found",
            "message": "远端未发现",
            "account": None,
        }
    except Exception as exc:
        logger.error("Sub2API Claude 查询异常: %s", exc)
        return {
            "ok": False, "found": False, "remote_state": "unreachable",
            "message": f"查询异常: {exc}",
            "account": None,
        }


# ── Create ─────────────────────────────────────────────────────────────────────


def _create_sub2api_claude_account(
    tokens: dict[str, Any],
    api_url: str,
    api_key: str,
    group_ids: list[int] | None = None,
    name: str | None = None,
) -> Tuple[bool, str]:
    resolved_gids = _parse_group_ids(group_ids)
    payload = _build_claude_account_payload(tokens, group_ids=resolved_gids, name=name)
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
        return False, _extract_error_message(response)
    except Exception as exc:
        logger.error("Sub2API Claude 上传异常: %s", exc)
        return False, f"上传异常: {exc}"


# ── Public Entry Points ────────────────────────────────────────────────────────


def import_claude_from_session_key(
    session_key: str,
    api_url: str | None = None,
    api_key: str | None = None,
    group_ids: list[int] | None = None,
    use_setup_token: bool = False,
    name: str | None = None,
) -> dict[str, Any]:
    """Import a Claude account to sub2api via sessionKey cookie exchange."""
    api_url, api_key = _resolve_sub2api_config(api_url, api_key)
    if not api_url:
        return {"ok": False, "message": "Sub2API API URL 未配置"}
    if not api_key:
        return {"ok": False, "message": "Sub2API API Key 未配置"}
    if not str(session_key or "").strip():
        return {"ok": False, "message": "sessionKey 不能为空"}
    if not str(session_key or "").strip().startswith(SESSION_KEY_PREFIX):
        return {"ok": False, "message": f"sessionKey 格式无效，应以 {SESSION_KEY_PREFIX} 开头"}

    exchange_result = _exchange_session_key(
        session_key, api_url, api_key, use_setup_token=use_setup_token
    )
    if not exchange_result["ok"]:
        return {"ok": False, "message": exchange_result["message"]}

    tokens = exchange_result["tokens"]
    email_address = str(tokens.get("email_address") or "").strip()

    if email_address:
        query_result = query_sub2api_claude_account(email_address, api_url=api_url, api_key=api_key)
        if query_result.get("ok") and query_result.get("found"):
            return {
                "ok": True,
                "message": "远端已存在，跳过上传",
                "email_address": email_address,
                "remote_account_id": query_result.get("remote_account_id"),
            }

    ok, msg = _create_sub2api_claude_account(
        tokens, api_url=api_url, api_key=api_key, group_ids=group_ids, name=name
    )
    return {"ok": ok, "message": msg, "email_address": email_address}


def import_claude_from_tokens(
    tokens: dict[str, Any],
    api_url: str | None = None,
    api_key: str | None = None,
    group_ids: list[int] | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Import a Claude account to sub2api using existing OAuth tokens."""
    api_url, api_key = _resolve_sub2api_config(api_url, api_key)
    if not api_url:
        return {"ok": False, "message": "Sub2API API URL 未配置"}
    if not api_key:
        return {"ok": False, "message": "Sub2API API Key 未配置"}

    access_token = str(tokens.get("access_token") or "").strip()
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        return {"ok": False, "message": "缺少 access_token 或 refresh_token"}

    email_address = str(tokens.get("email_address") or "").strip()
    if email_address:
        query_result = query_sub2api_claude_account(email_address, api_url=api_url, api_key=api_key)
        if query_result.get("ok") and query_result.get("found"):
            return {
                "ok": True,
                "message": "远端已存在，跳过上传",
                "email_address": email_address,
                "remote_account_id": query_result.get("remote_account_id"),
            }

    ok, msg = _create_sub2api_claude_account(
        tokens, api_url=api_url, api_key=api_key, group_ids=group_ids, name=name
    )
    return {"ok": ok, "message": msg, "email_address": email_address}


def sync_claude_sub2api_account(
    tokens: dict[str, Any],
    api_url: str | None = None,
    api_key: str | None = None,
    group_ids: list[int] | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Full sync: query + conditional create + verify for a Claude account."""
    api_url, api_key = _resolve_sub2api_config(api_url, api_key)
    if not api_url:
        return {"ok": False, "message": "Sub2API API URL 未配置", "remote_state": "unconfigured"}
    if not api_key:
        return {"ok": False, "message": "Sub2API API Key 未配置", "remote_state": "unconfigured"}

    email_address = str(tokens.get("email_address") or "").strip()
    if not email_address:
        return {"ok": False, "message": "缺少 email_address"}

    initial = query_sub2api_claude_account(email_address, api_url=api_url, api_key=api_key)
    if not initial.get("ok"):
        return {
            "ok": False,
            "remote_state": initial.get("remote_state") or "unreachable",
            "message": initial.get("message") or "查询失败",
        }
    if initial.get("found"):
        return {
            "ok": True,
            "remote_state": "exists",
            "message": "远端已存在，跳过上传",
            "remote_account_id": initial.get("remote_account_id"),
        }

    ok, msg = _create_sub2api_claude_account(
        tokens, api_url=api_url, api_key=api_key, group_ids=group_ids, name=name
    )
    if not ok:
        return {"ok": False, "remote_state": "not_found", "message": msg}

    verified = query_sub2api_claude_account(email_address, api_url=api_url, api_key=api_key)
    if verified.get("ok") and verified.get("found"):
        return {
            "ok": True,
            "remote_state": "exists",
            "message": "上传成功",
            "remote_account_id": verified.get("remote_account_id"),
        }
    return {
        "ok": True,
        "remote_state": "created_unverified",
        "message": f"上传成功，但校验失败: {verified.get('message') or 'unknown'}",
    }
