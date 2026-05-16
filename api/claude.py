"""Claude account import API endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/claude", tags=["claude"])


class SessionKeyImportRequest(BaseModel):
    session_key: str
    use_setup_token: bool = False
    name: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    group_ids: Optional[list[int]] = None


class TokenImportRequest(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: Optional[int] = None
    expires_in: Optional[int] = None
    token_type: str = "bearer"
    scope: str = ""
    org_uuid: str = ""
    account_uuid: str = ""
    email_address: str = ""
    name: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    group_ids: Optional[list[int]] = None


class BatchSessionKeyItem(BaseModel):
    session_key: str
    name: Optional[str] = None


class BatchSessionKeyImportRequest(BaseModel):
    items: list[BatchSessionKeyItem]
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    group_ids: Optional[list[int]] = None


@router.post("/import-from-session-key")
def import_from_session_key(req: SessionKeyImportRequest):
    from platforms.claude.sub2api_import import import_claude_from_session_key

    result = import_claude_from_session_key(
        session_key=req.session_key,
        api_url=req.api_url,
        api_key=req.api_key,
        group_ids=req.group_ids,
        use_setup_token=req.use_setup_token,
        name=req.name,
    )
    return result


@router.post("/import-from-tokens")
def import_from_tokens(req: TokenImportRequest):
    from platforms.claude.sub2api_import import import_claude_from_tokens

    tokens = {
        "access_token": req.access_token,
        "refresh_token": req.refresh_token,
        "expires_at": req.expires_at or 0,
        "expires_in": req.expires_in or 3600,
        "token_type": req.token_type,
        "scope": req.scope,
        "org_uuid": req.org_uuid,
        "account_uuid": req.account_uuid,
        "email_address": req.email_address,
    }
    result = import_claude_from_tokens(
        tokens=tokens,
        api_url=req.api_url,
        api_key=req.api_key,
        group_ids=req.group_ids,
        name=req.name,
    )
    return result


@router.post("/import-from-session-key/batch")
def batch_import_from_session_key(req: BatchSessionKeyImportRequest):
    from platforms.claude.sub2api_import import import_claude_from_session_key

    results = []
    success_count = 0
    for item in req.items:
        result = import_claude_from_session_key(
            session_key=item.session_key,
            api_url=req.api_url,
            api_key=req.api_key,
            group_ids=req.group_ids,
            name=item.name,
        )
        masked = f"{item.session_key[:16]}..." if len(item.session_key) > 16 else item.session_key
        results.append(
            {
                "session_key_masked": masked,
                "name": item.name,
                "ok": result.get("ok", False),
                "message": result.get("message", ""),
                "email_address": result.get("email_address", ""),
            }
        )
        if result.get("ok"):
            success_count += 1

    return {
        "total": len(req.items),
        "success": success_count,
        "failed": len(req.items) - success_count,
        "items": results,
    }


@router.get("/query-account")
def query_claude_account(
    email: str,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
):
    from platforms.claude.sub2api_import import query_sub2api_claude_account

    return query_sub2api_claude_account(email, api_url=api_url, api_key=api_key)
