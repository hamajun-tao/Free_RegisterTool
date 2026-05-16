"""ChatGPT 专用功能 API"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from pydantic import BaseModel
from typing import Optional
from core.db import AccountModel, get_session
from services.chatgpt_account_state import apply_chatgpt_status_policy
import json, sys, threading


router = APIRouter(prefix="/chatgpt", tags=["chatgpt"])

COUNTRIES = ["SG", "US", "TR", "JP", "HK", "GB", "AU", "CA", "IN", "BR", "MX"]


class UploadRequest(BaseModel):
    account_ids: list[int]
    cpa_api_url: Optional[str] = None
    cpa_api_token: Optional[str] = None
    team_manager_url: Optional[str] = None
    team_manager_key: Optional[str] = None


def _get_account(account_id: int, session: Session) -> AccountModel:
    acc = session.get(AccountModel, account_id)
    if not acc or acc.platform != "chatgpt":
        raise HTTPException(404, "账号不存在")
    return acc


def _to_codex_account(acc: AccountModel):
    """转换为 codex-register 的 Account 对象（duck-typing）"""
    extra = acc.get_extra()

    class _Acc:
        pass

    a = _Acc()
    a.email = acc.email
    a.access_token = extra.get("access_token") or acc.token
    a.refresh_token = extra.get("refresh_token", "")
    a.id_token = extra.get("id_token", "")
    a.session_token = extra.get("session_token", "")
    a.client_id = extra.get("client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
    a.cookies = extra.get("cookies", "")
    a.user_id = acc.user_id
    return a


def _persist_local_probe(acc: AccountModel, probe: dict, session: Session) -> None:
    extra = acc.get_extra()
    extra["chatgpt_local"] = probe
    acc.set_extra(extra)
    apply_chatgpt_status_policy(acc, local_probe=probe)
    from datetime import datetime
    acc.updated_at = datetime.utcnow()
    session.add(acc)
    session.commit()


# ── Token 刷新 ──────────────────────────────────────────────
@router.post("/{account_id}/refresh-token")
def refresh_token(account_id: int, proxy: Optional[str] = None,
                  session: Session = Depends(get_session)):
    acc = _get_account(account_id, session)
    codex_acc = _to_codex_account(acc)

    from platforms.chatgpt.token_refresh import TokenRefreshManager
    manager = TokenRefreshManager(proxy_url=proxy)
    result = manager.refresh_account(codex_acc)

    if result.success:
        extra = acc.get_extra()
        extra["access_token"] = result.access_token
        if result.refresh_token:
            extra["refresh_token"] = result.refresh_token
        acc.set_extra(extra)
        acc.token = result.access_token
        from datetime import datetime
        acc.updated_at = datetime.utcnow()
        session.add(acc)
        session.commit()
        return {"ok": True, "access_token": result.access_token[:40] + "..."}
    raise HTTPException(400, result.error_message)


# ── 生成支付链接 ────────────────────────────────────────────
class PaymentReq(BaseModel):
    plan: str = "plus"  # plus | team
    country: str = "SG"
    proxy: Optional[str] = None
    workspace_name: str = "MyTeam"
    seat_quantity: int = 5
    price_interval: str = "month"


@router.post("/{account_id}/payment-link")
def generate_payment_link(account_id: int, req: PaymentReq,
                          session: Session = Depends(get_session)):
    acc = _get_account(account_id, session)
    codex_acc = _to_codex_account(acc)

    from platforms.chatgpt.payment import generate_plus_link_via_mooizz, generate_team_link
    if req.plan == "plus":
        mooizz_result = generate_plus_link_via_mooizz(codex_acc, proxy=req.proxy, country=req.country)
        url = str(mooizz_result.get("url") or "").strip()
    else:
        url = generate_team_link(
            codex_acc, workspace_name=req.workspace_name,
            price_interval=req.price_interval, seat_quantity=req.seat_quantity,
            proxy=req.proxy, country=req.country
        )
    plan = str(req.plan or "plus").strip().lower()
    country = str(req.country or "SG").strip().upper()
    plan_label = "Plus" if plan == "plus" else "Team"
    description = f"ChatGPT {plan_label} payment link ({country})"

    extra = acc.get_extra()
    extra["cashier_url"] = url
    extra["payment_link"] = {
        "url": url,
        "plan": plan,
        "country": country,
        "description": description,
    }
    acc.cashier_url = url
    acc.set_extra(extra)
    from datetime import datetime
    acc.updated_at = datetime.utcnow()
    session.add(acc)
    session.commit()

    return {
        "url": url,
        "cashier_url": url,
        "plan": plan,
        "country": country,
        "description": description,
        "message": f"{plan_label} 长链接已生成，可直接打开或复制。",
    }


# ── 检查订阅状态 ────────────────────────────────────────────
@router.get("/{account_id}/subscription")
def check_subscription(account_id: int, proxy: Optional[str] = None,
                       session: Session = Depends(get_session)):
    acc = _get_account(account_id, session)
    codex_acc = _to_codex_account(acc)

    from platforms.chatgpt.status_probe import probe_local_chatgpt_status
    from core.config_store import config_store

    effective_proxy = proxy or str(config_store.get_all().get("proxy_url") or "").strip() or None
    probe = probe_local_chatgpt_status(codex_acc, proxy=effective_proxy)
    _persist_local_probe(acc, probe, session)
    return {
        "email": acc.email,
        "subscription": probe.get("subscription", {}).get("plan", "unknown"),
        "probe": probe,
    }


@router.post("/{account_id}/probe-local")
def probe_local_status(account_id: int, proxy: Optional[str] = None,
                       session: Session = Depends(get_session)):
    acc = _get_account(account_id, session)
    codex_acc = _to_codex_account(acc)

    from platforms.chatgpt.status_probe import probe_local_chatgpt_status
    from core.config_store import config_store

    effective_proxy = proxy or str(config_store.get_all().get("proxy_url") or "").strip() or None
    probe = probe_local_chatgpt_status(codex_acc, proxy=effective_proxy)
    _persist_local_probe(acc, probe, session)
    return {"ok": True, "email": acc.email, "probe": probe}


# ── CPA 上传 ────────────────────────────────────────────────
class CpaUploadReq(BaseModel):
    api_url: str
    api_key: str = ""


@router.post("/{account_id}/upload-cpa")
def upload_cpa(account_id: int, req: CpaUploadReq,
               session: Session = Depends(get_session)):
    acc = _get_account(account_id, session)
    codex_acc = _to_codex_account(acc)

    from platforms.chatgpt.cpa_upload import upload_to_cpa, generate_token_json
    token_data = generate_token_json(codex_acc)
    ok, msg = upload_to_cpa(token_data, api_url=req.api_url, api_key=req.api_key)
    return {"ok": ok, "message": msg}


class Sub2ApiUploadReq(BaseModel):
    api_url: str
    api_key: str = ""


@router.post("/{account_id}/upload-sub2api")
def upload_sub2api(account_id: int, req: Sub2ApiUploadReq,
                   session: Session = Depends(get_session)):
    acc = _get_account(account_id, session)
    codex_acc = _to_codex_account(acc)

    from platforms.chatgpt.sub2api_upload import upload_to_sub2api

    ok, msg = upload_to_sub2api(
        codex_acc,
        api_url=req.api_url,
        api_key=req.api_key,
    )
    return {"ok": ok, "message": msg}


# ── PayPal 预热登录 ──────────────────────────────────────────────────────

_paypal_prewarm_lock = threading.Lock()
_paypal_prewarm_status = {"running": False, "result": None}


@router.post("/paypal/prewarm-login")
def paypal_prewarm_login_api():
    """启动 PayPal 预热登录（弹出浏览器窗口，用户手动完成登录+OTP）"""
    if _paypal_prewarm_status["running"]:
        return {"ok": False, "message": "预热登录已在运行中", "status": "running"}

    from core.config_store import config_store
    cfg = config_store.get_all()
    email = (cfg.get("payment_paypal_email") or "").strip()
    password = (cfg.get("payment_paypal_password") or "").strip()
    proxy = (cfg.get("payment_paypal_proxy_url") or cfg.get("payment_promo_proxy_url") or "").strip()
    if not proxy:
        pool_str = (cfg.get("payment_proxy_pool") or "").strip()
        if pool_str:
            for part in pool_str.split(","):
                if ":" in part:
                    proxy = part.split(":", 1)[1].strip()
                    break

    def _run():
        _paypal_prewarm_status["running"] = True
        _paypal_prewarm_status["result"] = None
        try:
            from platforms.chatgpt.payment_auto import paypal_prewarm_login
            result = paypal_prewarm_login(
                email=email,
                password=password,
                proxy_url=proxy,
                timeout=300,
            )
            _paypal_prewarm_status["result"] = result
        except Exception as e:
            _paypal_prewarm_status["result"] = {"success": False, "message": str(e)}
        finally:
            _paypal_prewarm_status["running"] = False

    with _paypal_prewarm_lock:
        t = threading.Thread(target=_run, daemon=True)
        t.start()

    return {
        "ok": True,
        "message": f"已启动 PayPal 预热登录（邮箱={email}），请在弹出的浏览器窗口中完成登录",
        "status": "started",
    }


@router.get("/paypal/prewarm-status")
def paypal_prewarm_status_api():
    """查询 PayPal 预热登录状态"""
    return {
        "running": _paypal_prewarm_status["running"],
        "result": _paypal_prewarm_status["result"],
    }
