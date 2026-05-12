from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from typing import Optional
from datetime import datetime, timezone
import hashlib
import random
from core.db import TaskLog, engine
from core.task_runtime import SkipCurrentAttemptRequested, StopTaskRequested
import time, json, asyncio, threading, logging

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)

_tasks: dict = {}
_tasks_lock = threading.Lock()

MAX_FINISHED_TASKS = 200
CLEANUP_THRESHOLD = 250
TASK_LOG_LIMIT = 2000


def _cleanup_old_tasks():
    """Remove oldest finished tasks when the dict grows too large."""
    with _tasks_lock:
        finished = [
            (tid, t) for tid, t in _tasks.items()
            if t.get("status") in ("done", "failed")
        ]
        if len(finished) <= MAX_FINISHED_TASKS:
            return
        finished.sort(key=lambda x: x[0])
        to_remove = finished[: len(finished) - MAX_FINISHED_TASKS]
        for tid, _ in to_remove:
            del _tasks[tid]


class RegisterTaskRequest(BaseModel):
    platform: str
    email: Optional[str] = None
    password: Optional[str] = None
    count: int = Field(default=1, ge=1, le=1000)  # 最大支持 1000 个
    concurrency: int = Field(default=1, ge=1, le=50)  # 最大并发 50
    register_delay_seconds: float = Field(default=0, ge=0)
    random_delay_min: Optional[float] = Field(default=None, ge=0)  # 随机延迟最小值 (秒)
    random_delay_max: Optional[float] = Field(default=None, ge=0)  # 随机延迟最大值 (秒)
    proxy: Optional[str] = None
    executor_type: str = "protocol"
    captcha_solver: str = "yescaptcha"
    extra: dict = Field(default_factory=dict)
    # 定时任务配置
    task_id: Optional[str] = None  # 定时任务 ID（更新时使用）
    interval_type: Optional[str] = None  # minutes | hours
    interval_value: Optional[int] = None  # 间隔值
    # 并发控制配置
    email_retry_count: int = Field(default=3, ge=1, le=10)  # 邮箱创建重试次数
    stagger_seconds: float = Field(default=6.0, ge=0, le=60)  # Worker 错峰启动间隔(秒)
    email_max_concurrency: Optional[int] = Field(default=None, ge=1, le=20)


class TaskLogBatchDeleteRequest(BaseModel):
    ids: list[int]


def _log(task_id: str, msg: str):
    """向任务追加一条日志"""
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    with _tasks_lock:
        if task_id in _tasks:
            task = _tasks[task_id]
            logs = task.setdefault("logs", [])
            task["log_total"] = int(task.get("log_total", task.get("log_offset", 0) + len(logs))) + 1
            logs.append(entry)
            limit = max(1, int(TASK_LOG_LIMIT))
            if len(logs) > limit:
                drop_count = len(logs) - limit
                del logs[:drop_count]
                task["log_offset"] = int(task.get("log_offset", 0)) + drop_count
    try:
        print(entry)
    except UnicodeEncodeError as exc:
        encoding = exc.encoding or "utf-8"
        safe_entry = entry.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_entry)


def _save_task_log(platform: str, email: str, status: str,
                   error: str = "", detail: dict = None):
    """Write a TaskLog record to the database."""
    with Session(engine) as s:
        log = TaskLog(
            platform=platform,
            email=email,
            status=status,
            error=error,
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
        )
        s.add(log)
        s.commit()


def _set_worker_state(task_id: str, worker_index: int, **patch) -> None:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return
        states = task.setdefault("worker_states", [])
        while len(states) < worker_index:
            states.append(
                {
                    "index": len(states) + 1,
                    "state": "pending",
                    "stage": "pending",
                    "email": "",
                    "message": "",
                    "provider": "",
                    "proxy": "",
                    "updated_at": "",
                }
            )
        state = states[worker_index - 1]
        state.update(patch)
        state["index"] = worker_index
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        # O(1) 累加变更号，供 SSE progress 流签名比较用（不影响业务逻辑）
        task["worker_state_change_id"] = int(task.get("worker_state_change_id") or 0) + 1


def _resolve_mail_provider_sequence(value) -> list[str]:
    raw = str(value or "").strip()
    aliases = {
        "luckmail_cfworker": "luckmail,cfworker",
        "luckmail+cfworker": "luckmail,cfworker",
        "luckmail_cf": "luckmail,cfworker",
        "cf_luckmail": "cfworker,luckmail",
        "cfworker_luckmail": "cfworker,luckmail",
        "cfworker+luckmail": "cfworker,luckmail",
    }
    normalized = aliases.get(raw.lower(), raw)
    providers = [
        item.strip().lower()
        for item in normalized.replace("+", ",").replace("|", ",").replace(";", ",").split(",")
        if item.strip()
    ]
    return providers or ["laoudo"]


def _resolve_parallel_mail_mix(extra: dict | None) -> list[str]:
    raw = (extra or {}).get("mail_provider_mix")
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = str(raw or "").replace("+", ",").replace("|", ",").replace(";", ",").split(",")
    normalized = []
    seen = set()
    for item in values:
        provider = str(item or "").strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        normalized.append(provider)
    return normalized


class _TaskStore:
    """任务控制中心：支持停止和跳过当前条目。"""

    def __init__(self):
        self._stop: set = set()
        self._skip: set = set()
        self._pause: set = set()

    def request_stop(self, task_id: str) -> dict:
        self._stop.add(task_id)
        return self.snapshot(task_id)

    def request_skip_current(self, task_id: str) -> dict:
        self._skip.add(task_id)
        return self.snapshot(task_id)

    def request_pause(self, task_id: str) -> dict:
        self._pause.add(task_id)
        return self.snapshot(task_id)

    def request_resume(self, task_id: str) -> dict:
        self._pause.discard(task_id)
        return self.snapshot(task_id)

    def should_stop(self, task_id: str) -> bool:
        return task_id in self._stop

    def is_paused(self, task_id: str) -> bool:
        return task_id in self._pause

    def pop_skip(self, task_id: str) -> bool:
        """消费一次 skip 信号，返回是否命中。"""
        if task_id in self._skip:
            self._skip.discard(task_id)
            return True
        return False

    def clear(self, task_id: str) -> None:
        self._stop.discard(task_id)
        self._skip.discard(task_id)
        self._pause.discard(task_id)

    def snapshot(self, task_id: str) -> dict:
        with _tasks_lock:
            data = dict(_tasks.get(task_id, {}))
            if data:
                logs = list(data.get("logs", []))
                data["logs"] = logs
                data["log_offset"] = int(data.get("log_offset", 0))
                data["log_total"] = int(data.get("log_total", data["log_offset"] + len(logs)))
        if data:
            data["control"] = {
                "stop_requested": task_id in self._stop,
                "paused": task_id in self._pause,
                "pending_skip_requests": 1 if task_id in self._skip else 0,
                "targeted_skip_attempts": 0,
            }
        return data


_task_store = _TaskStore()


def _create_task_record(
    task_id: str,
    req: "RegisterTaskRequest",
    source: str,
    extra,
) -> None:
    """在 _tasks 里初始化一条任务记录（供内部和测试使用）。"""
    with _tasks_lock:
        _tasks[task_id] = {
            "id": task_id,
            "status": "pending",
            "progress": f"0/{req.count}",
            "total": req.count,
            "platform": req.platform,
            "started": 0,
            "completed": 0,
            "logs": [],
            "log_offset": 0,
            "log_total": 0,
            "success": 0,
            "skipped": 0,
            "errors": [],
            "source": source,
            "worker_states": [],
        }


def _auto_upload_integrations(task_id: str, account):
    """注册成功后自动导入外部系统。"""
    try:
        from services.external_sync import sync_account
        for result in sync_account(account):
            name = result.get("name", "Auto Upload")
            ok = bool(result.get("ok"))
            msg = result.get("msg", "")
            _log(task_id, f"  [{name}] {'[OK] ' + msg if ok else '[FAIL] ' + msg}")
    except Exception as e:
        _log(task_id, f"  [Auto Upload] 自动导入异常: {e}")


def _resolve_payment_plus_flow_order(extra: dict, cfg: dict | None = None) -> str:
    cfg = cfg or {}
    raw = (
        (extra or {}).get("payment_plus_flow_order")
        or cfg.get("payment_plus_flow_order")
        or "after_oauth"
    )
    normalized = str(raw or "").strip().lower().replace("-", "_")
    if normalized in {"before_oauth", "pre_oauth", "plus_before_oauth"}:
        return "before_oauth"
    return "after_oauth"


def _resolve_auto_pay_plan(extra: dict | None, cfg: dict | None = None) -> str:
    raw = (
        (extra or {}).get("auto_pay_plan")
        or (extra or {}).get("payment_auto_plan")
        or (cfg or {}).get("payment_auto_plan")
        or ""
    )
    return str(raw or "").strip().lower()


def _make_pre_oauth_auto_pay_hook(task_id: str, extra: dict):
    def _hook(result, runtime: dict) -> dict:
        from platforms.chatgpt.payment_auto import run_payment_from_config_store, PaymentError

        plan = 'plus'
        account_email = (getattr(result, 'email', '') or '').strip()
        billing_country = str((extra or {}).get('payment_billing_country') or 'US').strip().upper()
        billing_currency = str((extra or {}).get('payment_billing_currency') or 'USD').strip().upper()
        proxy_geo_country = str(
            runtime.get('proxy_geo_country')
            or (extra or {}).get('proxy_geo_country')
            or ''
        ).strip().upper()
        proxy_url = (
            (extra or {}).get('proxy')
            or (extra or {}).get('proxy_url')
            or runtime.get('proxy_url')
            or ''
        ).strip()
        idempotency_key = _build_payment_idempotency_key(
            account_email=account_email,
            plan=plan,
            billing_country=billing_country,
            billing_currency=billing_currency,
        )
        geo_diagnostics = _build_payment_geo_diagnostics(
            billing_country=billing_country,
            billing_currency=billing_currency,
            proxy_url=proxy_url,
            proxy_geo_country=proxy_geo_country,
        )

        _log(task_id, '  [AutoPay] pre-oauth payment hook')
        access_token = (getattr(result, 'access_token', '') or '').strip()
        session_token = (runtime.get('session_token') or getattr(result, 'session_token', '') or '').strip()
        cookie_header = (runtime.get('cookie_header') or '').strip()
        device_id = (runtime.get('device_id') or '').strip()
        cookie_count = len([part for part in cookie_header.split(';') if part.strip()])
        _log(
            task_id,
            '  [AutoPay] OAuth 前支付凭证检查'
            f" access_token={'yes' if access_token else 'no'}"
            f" session_token={'yes' if session_token else 'no'}"
            f" cookie_header={'yes' if cookie_header else 'no'}"
            f" cookie_count={cookie_count}"
            f" device_id={'yes' if device_id else 'no'}",
        )
        if not (access_token or session_token):
            message = 'missing ChatGPT access_token/session_token before OAuth'
            _log(task_id, f'  [AutoPay] {message}')
            return {
                'plan': plan,
                'state': 'skipped_pre_oauth_missing_auth',
                'flow_order': 'before_oauth',
                'error': message,
                'idempotency_key': idempotency_key,
                'geo_diagnostics': geo_diagnostics,
            }

        try:
            payment_result = run_payment_from_config_store(
                plan_name='chatgptplusplan',
                access_token=access_token,
                session_token=session_token,
                cookie_header=cookie_header,
                device_id=device_id,
                proxy_url=proxy_url,
                proxy_geo_country=proxy_geo_country,
                config_overrides=extra,
                log_fn=lambda msg: _log(task_id, f'  [AutoPay] {msg}'),
            )
        except PaymentError as pe:
            _log(task_id, f'  [AutoPay] pre-oauth payment failed: {pe}')
            return {
                'plan': plan,
                'state': 'failed_pre_oauth',
                'flow_order': 'before_oauth',
                'error': str(pe),
                'idempotency_key': idempotency_key,
                'geo_diagnostics': geo_diagnostics,
            }

        if payment_result.success:
            receipt = payment_result.receipt_url[:80] if payment_result.receipt_url else ''
            receipt_suffix = f' receipt={receipt}' if receipt else ''
            _log(task_id, f'  [AutoPay] pre-oauth payment succeeded state={payment_result.state}{receipt_suffix}')
        else:
            _log(task_id, f'  [AutoPay] pre-oauth payment failed state={payment_result.state} error={payment_result.error}')

        return {
            'plan': plan,
            'state': payment_result.state if payment_result.success else f'failed_pre_oauth:{payment_result.state}',
            'receipt_url': payment_result.receipt_url,
            'flow_order': 'before_oauth',
            'error': payment_result.error,
            'idempotency_key': idempotency_key,
            'geo_diagnostics': geo_diagnostics,
        }

    return _hook

def _auto_pay_after_register(task_id: str, account, extra: dict):
    try:
        platform = getattr(account, 'platform', '') or ''
        if platform != 'chatgpt':
            return

        from core.config_store import config_store
        cfg = config_store.get_all()

        plan = _resolve_auto_pay_plan(extra, cfg)
        if plan not in ('plus', 'team'):
            return

        account_extra = _load_account_extra(account)
        billing_country = str((extra.get('payment_billing_country') or cfg.get('payment_billing_country') or 'US')).strip().upper()
        billing_currency = str((extra.get('payment_billing_currency') or cfg.get('payment_billing_currency') or 'USD')).strip().upper()
        proxy_geo_country = str(
            account_extra.get('proxy_geo_country')
            or extra.get('proxy_geo_country')
            or ''
        ).strip().upper()
        proxy_url = str((extra.get('proxy') or extra.get('proxy_url') or '')).strip()
        idempotency_key = _build_payment_idempotency_key(
            account_email=getattr(account, 'email', ''),
            plan=plan,
            billing_country=billing_country,
            billing_currency=billing_currency,
        )
        geo_diagnostics = _build_payment_geo_diagnostics(
            billing_country=billing_country,
            billing_currency=billing_currency,
            proxy_url=proxy_url,
            proxy_geo_country=proxy_geo_country,
        )

        existing_state = str(account_extra.get('auto_pay_state') or '').strip()
        existing_key = str(account_extra.get('auto_pay_idempotency_key') or '').strip()
        existing_flow_order = str(account_extra.get('auto_pay_flow_order') or '').strip()
        if (
            existing_state == 'succeeded'
            and (
                existing_flow_order == 'before_oauth'
                or not existing_key
                or existing_key == idempotency_key
            )
        ):
            _persist_account_extra(task_id, account, {}, status='subscribed')
            _log(task_id, f'  [AutoPay] duplicate succeeded payment skipped key={idempotency_key}')
            return

        plan_name = 'chatgptplusplan' if plan == 'plus' else 'chatgptteamplan'
        _log(task_id, f'  [AutoPay] start payment plan={plan}')

        access_token = (account_extra.get('access_token') or '').strip()
        session_token = (account_extra.get('session_token') or account_extra.get('refresh_token') or '').strip()
        cookie_header = (account_extra.get('cookie_header') or '').strip()
        device_id = (account_extra.get('oai_device_id') or account_extra.get('device_id') or '').strip()

        if not access_token:
            _log(task_id, '  [AutoPay] skip: missing access_token')
            return

        from platforms.chatgpt.payment_auto import run_payment_from_config_store, PaymentError

        # 代理池：逗号分隔，支持地区标签 geo:url（如 jp:http://..., eu:http://...）
        proxy_pool_raw = str(extra.get('payment_proxy_pool') or cfg.get('payment_proxy_pool') or '').strip()
        proxy_pool = []
        if proxy_pool_raw:
            for _entry in proxy_pool_raw.split(','):
                _entry = _entry.strip()
                if not _entry:
                    continue
                # 剥离 geo 标签，只保留 URL 用于轮换
                if ':' in _entry and not _entry.startswith('http'):
                    _url = _entry.split(':', 1)[1].strip()
                else:
                    _url = _entry
                if _url:
                    proxy_pool.append(_url)
        max_retries = 2
        try:
            max_retries = int(extra.get('payment_max_retries') or cfg.get('payment_max_retries') or 2)
        except Exception:
            pass

        # 按 provider 分组的重试策略：diagnostic_code → (should_retry, retry_delay_s, rotate_proxy)
        from platforms.chatgpt.payment_auto import resolve_provider
        _resolved_provider, _ = resolve_provider({**cfg, **extra})
        _log(task_id, f'  [AutoPay] resolved provider={_resolved_provider}')

        # manual_link: 永不重试
        if _resolved_provider == 'manual_link':
            max_retries = 1

        _RETRY_STRATEGIES_PAYPAL_WEB = {
            'datadome_slider': (True, 3, True),
            'datadome_ip_blocked': (True, 3, True),
            'datadome_slider_failed': (True, 3, True),
            'hcaptcha_timeout': (True, 10, False),
            'hcaptcha_failed': (True, 10, False),
            'hcaptcha_paypal_failed': (True, 10, False),
            'paypal_callback_timeout': (True, 5, False),
            'paypal_browser_auth': (True, 5, True),
            'skipped_not_free': (True, 5, True),   # promo 未生效时可换 IP 重试
        }
        _RETRY_STRATEGIES_GOPAY_API = {
            'gopay_otp_timeout': (True, 5, False),
            'gopay_pin_failed': (True, 3, False),
            'gopay_linking_failed': (True, 5, False),
        }
        _RETRY_STRATEGIES_GOPAY_ANDROID = {
            'emulator_boot_timeout': (True, 10, False),
            'network_down': (True, 5, False),
            'app_launch_failed': (True, 5, False),
            'otp_input_timeout': (True, 10, False),
            'otp_sms_read_failed': (True, 10, False),
            'otp_verify_failed': (True, 5, False),
            'login_ui_not_found': (True, 5, False),
            'pin_verify_failed': (True, 3, False),
            'payment_confirm_timeout': (True, 5, False),
            # 配置缺失不可重试，由前端/用户补配置
            # 'no_phone_number', 'no_otp_provider', 'no_gopay_pin' → not in table = no retry
        }
        _RETRY_BY_PROVIDER = {
            'paypal_web': _RETRY_STRATEGIES_PAYPAL_WEB,
            'gopay_api': _RETRY_STRATEGIES_GOPAY_API,
            'gopay_android': _RETRY_STRATEGIES_GOPAY_ANDROID,
            'card': _RETRY_STRATEGIES_PAYPAL_WEB,  # card 共用 checkout 侧策略
        }
        _RETRY_STRATEGIES = _RETRY_BY_PROVIDER.get(_resolved_provider, {})

        result = None
        current_proxy = proxy_url
        proxy_idx = 0
        for attempt in range(1, max_retries + 1):
            try:
                result = run_payment_from_config_store(
                    plan_name=plan_name,
                    access_token=access_token,
                    session_token=session_token,
                    cookie_header=cookie_header,
                    device_id=device_id,
                    proxy_url=current_proxy,
                    proxy_geo_country=proxy_geo_country,
                    config_overrides=extra,
                    log_fn=lambda msg: _log(task_id, f'  [AutoPay] {msg}'),
                )
            except PaymentError as pe:
                _log(task_id, f'  [AutoPay] payment exception (attempt {attempt}/{max_retries}): {pe}')
                result = None
                break

            if result and result.success:
                break

            # 检查是否应该重试
            diag = getattr(result, 'diagnostic_code', '') if result else ''
            retryable = getattr(result, 'retryable', False) if result else False
            strategy = _RETRY_STRATEGIES.get(diag)

            if attempt < max_retries and (retryable or strategy):
                should_retry, delay, rotate = strategy if strategy else (retryable, 5, False)
                if should_retry:
                    _log(task_id, f'  [AutoPay] retrying ({attempt}/{max_retries}) diag={diag} delay={delay}s rotate_proxy={rotate}')
                    if rotate and proxy_pool:
                        proxy_idx = (proxy_idx + 1) % len(proxy_pool)
                        current_proxy = proxy_pool[proxy_idx]
                        _log(task_id, f'  [AutoPay] switched proxy to: {current_proxy[:40]}...')
                    import time as _time
                    _time.sleep(delay)
                    continue
            break

        if result is None:
            return

        # 持久化结果（含诊断信息）
        def _safe_result_text(name: str, default: str = '') -> str:
            value = getattr(result, name, default)
            if value is None:
                return default
            if isinstance(value, (str, int, float, bool)):
                return str(value)
            return default

        diag_code = _safe_result_text('diagnostic_code')
        stage = _safe_result_text('stage')
        provider = _safe_result_text('provider', _resolved_provider) or _resolved_provider
        state = _safe_result_text('state')
        receipt_url = _safe_result_text('receipt_url')
        error_text = _safe_result_text('error')

        if result.success:
            update = {
                'auto_pay_plan': plan,
                'auto_pay_state': state,
                'auto_pay_receipt': receipt_url,
                'auto_pay_flow_order': extra.get('auto_pay_flow_order') or _resolve_payment_plus_flow_order(extra, cfg),
                'auto_pay_idempotency_key': idempotency_key,
                'auto_pay_geo_diagnostics': geo_diagnostics,
                'auto_pay_provider': provider,
            }
            _persist_account_extra(
                task_id,
                account,
                update,
                status='subscribed' if state == 'succeeded' else None,
            )
        else:
            fail_update = {
                'auto_pay_plan': plan,
                'auto_pay_state': f'failed:{state}',
                'auto_pay_diagnostic_code': diag_code,
                'auto_pay_stage': stage,
                'auto_pay_provider': provider,
                'auto_pay_error': error_text[:200],
                'auto_pay_flow_order': extra.get('auto_pay_flow_order') or _resolve_payment_plus_flow_order(extra, cfg),
                'auto_pay_idempotency_key': idempotency_key,
                'auto_pay_geo_diagnostics': geo_diagnostics,
            }
            _persist_account_extra(task_id, account, fail_update)
            _log(task_id, f'  [AutoPay] payment failed state={state} diag={diag_code} stage={stage} error={error_text}')
    except Exception as e:
        _log(task_id, f'  [AutoPay] exception: {e}')

def _run_register(task_id: str, req: RegisterTaskRequest):
    from core.registry import get
    from core.base_platform import RegisterConfig
    from core.db import save_account
    from core.base_mailbox import create_mailbox

    with _tasks_lock:
        _tasks[task_id]["status"] = "running"
    success = 0
    skipped = 0
    errors = []

    try:
        _preflight_auto_pay_config(req)
    except HTTPException as exc:
        msg = str(exc.detail)
        _log(task_id, msg)
        with _tasks_lock:
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["success"] = 0
            _tasks[task_id]["skipped"] = 0
            _tasks[task_id]["errors"] = [msg]
        return

    # === 并发控制参数 ===
    max_workers = min(req.concurrency, req.count)
    email_sem = threading.Semaphore(min(req.email_max_concurrency or max_workers, max_workers))
    email_min_interval = 2.0  # LuckMail API 最小调用间隔(秒)
    email_retries = req.email_retry_count
    stagger_seconds = req.stagger_seconds
    last_email_call = [0.0]  # 可变闭包记录上次邮箱创建时间

    # === 线程安全数据结构 ===
    import queue
    result_queue = queue.Queue()
    stop_event = threading.Event()

    def _wait_while_paused() -> bool:
        logged_pause = False
        while _task_store.is_paused(task_id):
            if stop_event.is_set() or _task_store.should_stop(task_id):
                stop_event.set()
                return False
            if not logged_pause:
                _log(task_id, "任务已暂停，等待恢复...")
                logged_pause = True
            time.sleep(0.25)
        if logged_pause:
            _log(task_id, "任务已恢复，继续执行")
        return True

    class _TaskControlBridge:
        """Bridge task stop/skip requests into cooperative platform checkpoints."""

        def __init__(self, task_id: str):
            self.task_id = task_id
            self._lock = threading.Lock()
            self._next_attempt_id = 1

        def start_attempt(self) -> int:
            with self._lock:
                attempt_id = self._next_attempt_id
                self._next_attempt_id += 1
                return attempt_id

        def finish_attempt(self, attempt_id: int | None) -> None:
            return None

        def checkpoint(self, *, consume_skip: bool = True, attempt_id: int | None = None) -> None:
            if stop_event.is_set() or _task_store.should_stop(self.task_id):
                stop_event.set()
                raise StopTaskRequested()
            if not _wait_while_paused():
                raise StopTaskRequested()
            if stop_event.is_set() or _task_store.should_stop(self.task_id):
                stop_event.set()
                raise StopTaskRequested()
            if consume_skip and _task_store.pop_skip(self.task_id):
                raise SkipCurrentAttemptRequested()

        def is_stop_requested(self) -> bool:
            return stop_event.is_set() or _task_store.should_stop(self.task_id)

    task_control = _TaskControlBridge(task_id)

    try:
        PlatformCls = get(req.platform)
        parallel_mail_mix = _resolve_parallel_mail_mix(req.extra)
        if parallel_mail_mix:
            shuffled_parallel_mail_mix = list(parallel_mail_mix)
            random.shuffle(shuffled_parallel_mail_mix)
        else:
            shuffled_parallel_mail_mix = []

        def _build_mailbox(proxy: Optional[str], index: int, attempt: int = 0):
            from core.config_store import config_store
            merged_extra = config_store.get_all().copy()
            merged_extra.update({k: v for k, v in req.extra.items() if v is not None})
            if shuffled_parallel_mail_mix:
                providers = shuffled_parallel_mail_mix
            else:
                providers = _resolve_mail_provider_sequence(merged_extra.get("mail_provider", "laoudo"))
            provider = providers[(index + attempt) % len(providers)]
            merged_extra["mail_provider"] = provider
            return create_mailbox(
                provider=provider,
                extra=merged_extra,
                proxy=proxy,
            )

        # === 消费者：注册 Worker（接收预创建的 mailbox） ===
        def _do_one(i: int, _mailbox, _proxy: str):
            # 停止/跳过信号
            if stop_event.is_set() or _task_store.should_stop(task_id):
                result_queue.put(("__stop_or_skip__", i, _task_store.pop_skip(task_id)))
                return
            if not _wait_while_paused():
                result_queue.put(("__stop_or_skip__", i, False))
                return

            # 错峰启动：分段 sleep 以响应 stop 信号
            if i > 0 and stagger_seconds > 0:
                delay = i * stagger_seconds
                _log(task_id, f"Worker-{i} 错峰启动，等待 {delay:.1f}s")
                slept = 0.0
                while slept < delay:
                    if stop_event.is_set() or _task_store.should_stop(task_id):
                        result_queue.put(("__stop_or_skip__", i, _task_store.pop_skip(task_id)))
                        return
                    if not _wait_while_paused():
                        result_queue.put(("__stop_or_skip__", i, False))
                        return
                    time.sleep(0.25)
                    slept += 0.25

            # 每轮循环检查停止/跳过
            if stop_event.is_set() or _task_store.should_stop(task_id):
                result_queue.put(("__stop_or_skip__", i, _task_store.pop_skip(task_id)))
                return
            if not _wait_while_paused():
                result_queue.put(("__stop_or_skip__", i, False))
                return
            if _task_store.pop_skip(task_id):
                result_queue.put(("__skip__", i, False))
                return

            attempt_token = None
            try:
                from core.proxy_pool import proxy_pool
                from core.config_store import config_store

                merged_extra = config_store.get_all().copy()
                merged_extra.update({k: v for k, v in req.extra.items() if v is not None})
                auto_pay_plan = str(
                    merged_extra.get("auto_pay_plan")
                    or merged_extra.get("payment_auto_plan")
                    or ""
                ).strip().lower()
                if (
                    req.platform == "chatgpt"
                    and auto_pay_plan == "plus"
                    and _resolve_payment_plus_flow_order(merged_extra, merged_extra) == "before_oauth"
                ):
                    merged_extra = dict(merged_extra)
                    hook_extra = dict(merged_extra)
                    if _proxy:
                        hook_extra.setdefault("proxy", _proxy)
                        hook_extra.setdefault("proxy_url", _proxy)
                    merged_extra["_chatgpt_pre_oauth_auto_pay_hook"] = _make_pre_oauth_auto_pay_hook(
                        task_id,
                        hook_extra,
                    )
                    _log(task_id, "  [AutoPay] 已启用 Plus OAuth 前升级模式")

                _config = RegisterConfig(
                    executor_type=req.executor_type,
                    captcha_solver=req.captcha_solver,
                    proxy=_proxy,
                    extra=merged_extra,
                )
                _platform = PlatformCls(config=_config, mailbox=_mailbox)
                attempt_token = task_control.start_attempt()
                try:
                    _platform.bind_task_control(task_control)
                    _platform._task_attempt_token = attempt_token
                    if getattr(_platform, "mailbox", None) is not None:
                        _platform.mailbox._task_attempt_token = attempt_token
                except Exception:
                    pass
                _platform._log_fn = lambda msg: _log(task_id, msg)
                if getattr(_platform, "mailbox", None) is not None:
                    _platform.mailbox._log_fn = _platform._log_fn
                with _tasks_lock:
                    _tasks[task_id]["started"] = max(int(_tasks[task_id].get("started") or 0), i + 1)
                _set_worker_state(
                    task_id,
                    i + 1,
                    state="running",
                    stage="registering",
                    proxy=str(_proxy or ""),
                    message="registering",
                )
                _log(task_id, f"开始注册第 {i+1}/{req.count} 个账号")
                if _proxy:
                    _log(task_id, f"使用代理: {_proxy}")
                account = _platform.register(
                    email=req.email or None,
                    password=req.password,
                )
                if isinstance(account.extra, dict):
                    mail_provider = merged_extra.get("mail_provider", "")
                    if mail_provider:
                        account.extra.setdefault("mail_provider", mail_provider)
                    if mail_provider == "luckmail" and req.platform == "chatgpt":
                        mailbox_token = getattr(_mailbox, "_token", "") or ""
                        if mailbox_token:
                            account.extra.setdefault("mailbox_token", mailbox_token)
                        if merged_extra.get("luckmail_project_code"):
                            account.extra.setdefault("luckmail_project_code", merged_extra.get("luckmail_project_code"))
                        if merged_extra.get("luckmail_email_type"):
                            account.extra.setdefault("luckmail_email_type", merged_extra.get("luckmail_email_type"))
                        if merged_extra.get("luckmail_domain"):
                            account.extra.setdefault("luckmail_domain", merged_extra.get("luckmail_domain"))
                        if merged_extra.get("luckmail_base_url"):
                            account.extra.setdefault("luckmail_base_url", merged_extra.get("luckmail_base_url"))
                saved_account = save_account(account)
                _set_worker_state(
                    task_id,
                    i + 1,
                    state="success",
                    stage="done",
                    email=str(account.email or ""),
                    message="registered",
                )
                if _proxy:
                    # 使用智能选择器报告成功
                    use_smart_selector = _cfg_store.get("use_smart_proxy_selector", "1").strip() in {"1", "true", "yes"}
                    if use_smart_selector:
                        from core.smart_proxy_selector import smart_selector
                        smart_selector.report_proxy_result(_proxy, success=True)
                    else:
                        proxy_pool.report_success(_proxy)
                _log(task_id, f"[OK] 注册成功: {account.email}")
                _save_task_log(req.platform, account.email, "success")
                
                # 1. 先执行自动支付升级（仅 Plus/Team 配置生效；Free 会立即返回）
                auto_pay_extra = dict(merged_extra)
                if _proxy:
                    auto_pay_extra.setdefault("proxy", _proxy)
                    auto_pay_extra.setdefault("proxy_url", _proxy)
                _auto_pay_after_register(task_id, saved_account or account, auto_pay_extra)

                cashier_url = (account.extra or {}).get("cashier_url", "")
                if cashier_url:
                    _log(task_id, f"  [升级链接] {cashier_url}")
                    with _tasks_lock:
                        _tasks[task_id].setdefault("cashier_urls", []).append(cashier_url)
                result_queue.put(("__success__", i, True))

                # 2. 外部同步不阻塞注册完成计数，避免 Free 注册被 CPA/Sub2API 网络拖慢。
                def _upload_later():
                    _auto_upload_integrations(task_id, saved_account or account)

                threading.Thread(target=_upload_later, daemon=True).start()
            except StopTaskRequested as e:
                stop_event.set()
                _set_worker_state(
                    task_id,
                    i + 1,
                    state="stopped",
                    stage="stopped",
                    message=str(e),
                )
                _log(task_id, str(e))
                result_queue.put(("__stop_or_skip__", i, False))
            except SkipCurrentAttemptRequested as e:
                _set_worker_state(
                    task_id,
                    i + 1,
                    state="skipped",
                    stage="skipped",
                    message=str(e),
                )
                _log(task_id, str(e))
                result_queue.put(("__skip__", i, False))
            except Exception as e:
                if _proxy:
                    try:
                        # 使用智能选择器报告失败
                        use_smart_selector = _cfg_store.get("use_smart_proxy_selector", "1").strip() in {"1", "true", "yes"}
                        if use_smart_selector:
                            from core.smart_proxy_selector import smart_selector
                            smart_selector.report_proxy_result(_proxy, success=False, auto_blacklist=True)
                        else:
                            from core.proxy_pool import proxy_pool
                            proxy_pool.report_fail(_proxy)
                    except Exception:
                        pass
                _log(task_id, f"[FAIL] 注册失败: {e}")
                _set_worker_state(
                    task_id,
                    i + 1,
                    state="error",
                    stage="failed",
                    message=str(e),
                )
                _save_task_log(req.platform, req.email or "", "failed", error=str(e))
                result_queue.put(("__error__", i, str(e)))
            finally:
                task_control.finish_attempt(attempt_token)

        # === 生产者-消费者流水线 ===
        from concurrent.futures import ThreadPoolExecutor

        inflight_registration_slots = threading.Semaphore(max_workers)

        def _acquire_registration_slot() -> bool:
            while not stop_event.is_set() and not _task_store.should_stop(task_id):
                if not _wait_while_paused():
                    return False
                if inflight_registration_slots.acquire(timeout=0.25):
                    return True
            return False

        _do_one_without_slot_release = _do_one

        def _do_one_and_release_slot(i: int, _mailbox, _proxy: str):
            try:
                _do_one_without_slot_release(i, _mailbox, _proxy)
            finally:
                inflight_registration_slots.release()

        _do_one = _do_one_and_release_slot

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            submitted_count = 0  # 已提交到线程池的数量
            futures = []

            for i in range(req.count):
                if not _acquire_registration_slot():
                    stop_event.set()
                    break
                if not _wait_while_paused():
                    stop_event.set()
                    break
                if stop_event.is_set() or _task_store.should_stop(task_id):
                    stop_event.set()
                    break

                # --- 邮箱创建（带 Semaphore 限流 + 重试） ---
                mailbox = None
                for attempt in range(email_retries):
                    if not _wait_while_paused():
                        stop_event.set()
                        break
                    if stop_event.is_set() or _task_store.should_stop(task_id):
                        stop_event.set()
                        break

                    try:
                        with email_sem:
                            # 确保最小调用间隔
                            now = time.time()
                            elapsed = now - last_email_call[0]
                            _log(task_id, f"正在创建第 {i+1}/{req.count} 个邮箱...")
                            if elapsed < email_min_interval:
                                time.sleep(email_min_interval - elapsed)
                            mailbox = _build_mailbox(req.proxy or None, i, attempt)
                            last_email_call[0] = time.time()
                            provider_name = getattr(getattr(mailbox, "service_type", None), "value", None) or getattr(mailbox, "__class__", type(mailbox)).__name__
                            _set_worker_state(
                                task_id,
                                i + 1,
                                state="ready",
                                stage="mailbox_ready",
                                provider=str(provider_name or ""),
                                message="mailbox ready",
                            )
                            _log(task_id, f"第 {i+1}/{req.count} 个邮箱创建成功 provider={provider_name}")
                            break
                    except Exception as e:
                        error_str = str(e)
                        if attempt < email_retries - 1:
                            backoff = (2 ** attempt) * max(email_min_interval, 2.0)
                            if "rate" in error_str.lower() or "频率" in error_str:
                                _log(task_id, f"邮箱创建触发频率限制，{backoff:.0f}s 后重试 ({attempt + 1}/{email_retries})")
                            else:
                                _log(task_id, f"邮箱创建失败，{backoff:.0f}s 后重试 ({attempt + 1}/{email_retries}): {e}")
                            time.sleep(backoff)
                        else:
                            _log(task_id, f"[FAIL] 邮箱创建重试耗尽 ({email_retries}次): {e}")

                if mailbox is None:
                    errors.append(f"第{i + 1}个账号邮箱创建失败")
                    result_queue.put(("__error__", i, "邮箱创建失败"))
                    submitted_count += 1
                    inflight_registration_slots.release()
                    continue

                if stop_event.is_set() or _task_store.should_stop(task_id):
                    stop_event.set()
                    inflight_registration_slots.release()
                    break
                if not _wait_while_paused():
                    stop_event.set()
                    inflight_registration_slots.release()
                    break

                # 解析代理
                from core.proxy_pool import proxy_pool
                from core.config_store import config_store as _cfg_store
                
                _proxy = req.proxy
                if not _proxy:
                    # 检查是否启用智能代理选择
                    use_smart_selector = _cfg_store.get("use_smart_proxy_selector", "1").strip() in {"1", "true", "yes"}
                    
                    if use_smart_selector:
                        from core.smart_proxy_selector import smart_selector
                        _proxy = smart_selector.get_smart_proxy(
                            region="",
                            avoid_adjacent_ports=True,
                            randomize=True,
                            min_success_rate=0.3  # 优先选择成功率 > 30% 的代理
                        )
                    else:
                        _proxy = proxy_pool.get_next()

                # 立即提交 Worker（流水线：不等待其他邮箱）
                futures.append(pool.submit(_do_one, i, mailbox, _proxy))
                submitted_count += 1

            # --- 结果收集 ---
            processed = 0
            while processed < submitted_count:
                try:
                    result = result_queue.get(timeout=5)
                except queue.Empty:
                    if futures and not all(f.done() for f in futures):
                        continue
                    missing = submitted_count - processed
                    if missing > 0:
                        errors.append(f"{missing} 个账号流程结束但未返回结果")
                    break

                tag = result[0]

                if tag == "__success__":
                    success += 1
                elif tag == "__error__":
                    errors.append(result[2])
                elif tag == "__skip__":
                    skipped += 1
                elif tag == "__stop_or_skip__":
                    _, idx, was_skip = result
                    if was_skip:
                        skipped += 1
                    else:
                        with _tasks_lock:
                            _tasks[task_id]["status"] = "stopped"
                            _tasks[task_id]["completed"] = processed
                            _tasks[task_id]["progress"] = f"{processed}/{req.count}"
                            _tasks[task_id]["success"] = success
                            _tasks[task_id]["skipped"] = skipped
                            _tasks[task_id]["errors"] = errors
                        _task_store.clear(task_id)
                        return
                processed += 1
                with _tasks_lock:
                    _tasks[task_id]["completed"] = processed
                    _tasks[task_id]["progress"] = f"{processed}/{req.count}"
                    _tasks[task_id]["success"] = success
                    _tasks[task_id]["skipped"] = skipped
                    _tasks[task_id]["errors"] = list(errors)

    except Exception as e:
        _log(task_id, f"致命错误: {e}")
        with _tasks_lock:
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["error"] = str(e)
        return

    final_status = "stopped" if (stop_event.is_set() or _task_store.should_stop(task_id)) else "done"
    with _tasks_lock:
        _tasks[task_id]["status"] = final_status
        _tasks[task_id]["success"] = success
        _tasks[task_id]["skipped"] = skipped
        _tasks[task_id]["errors"] = errors
    if final_status == "stopped":
        _log(task_id, f"任务已停止: 成功 {success} 个, 跳过 {skipped} 个, 失败 {len(errors)} 个")
    else:
        _log(task_id, f"完成: 成功 {success} 个, 失败 {len(errors)} 个")
    _task_store.clear(task_id)
    _cleanup_old_tasks()

_PAYMENT_GEO_HINTS = {
    "US": ("en-US", "America/New_York"),
    "CA": ("en-CA", "America/Toronto"),
    "GB": ("en-GB", "Europe/London"),
    "DE": ("de-DE", "Europe/Berlin"),
    "FR": ("fr-FR", "Europe/Paris"),
    "ES": ("es-ES", "Europe/Madrid"),
    "IT": ("it-IT", "Europe/Rome"),
    "NL": ("nl-NL", "Europe/Amsterdam"),
    "JP": ("ja-JP", "Asia/Tokyo"),
    "KR": ("ko-KR", "Asia/Seoul"),
    "HK": ("zh-HK", "Asia/Hong_Kong"),
    "TW": ("zh-TW", "Asia/Taipei"),
    "SG": ("en-SG", "Asia/Singapore"),
    "AU": ("en-AU", "Australia/Sydney"),
    "NZ": ("en-NZ", "Pacific/Auckland"),
    "IN": ("en-IN", "Asia/Kolkata"),
    "ID": ("id-ID", "Asia/Jakarta"),
    "MY": ("ms-MY", "Asia/Kuala_Lumpur"),
    "PH": ("en-PH", "Asia/Manila"),
    "TH": ("th-TH", "Asia/Bangkok"),
    "VN": ("vi-VN", "Asia/Ho_Chi_Minh"),
    "TR": ("tr-TR", "Europe/Istanbul"),
    "BR": ("pt-BR", "America/Sao_Paulo"),
    "MX": ("es-MX", "America/Mexico_City"),
    "AR": ("es-AR", "America/Argentina/Buenos_Aires"),
    "CL": ("es-CL", "America/Santiago"),
    "CO": ("es-CO", "America/Bogota"),
    "AE": ("ar-AE", "Asia/Dubai"),
    "IL": ("he-IL", "Asia/Jerusalem"),
    "ZA": ("en-ZA", "Africa/Johannesburg"),
}


def _normalize_payment_component(value) -> str:
    return str(value or "").strip()


def _build_payment_idempotency_key(*, account_email: str, plan: str, billing_country: str, billing_currency: str) -> str:
    payload = "|".join(
        [
            _normalize_payment_component(account_email).lower(),
            _normalize_payment_component(plan).lower(),
            _normalize_payment_component(billing_country).upper(),
            _normalize_payment_component(billing_currency).upper(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_payment_geo_diagnostics(
    *,
    billing_country: str,
    billing_currency: str,
    proxy_url: str = "",
    proxy_geo_country: str = "",
) -> dict:
    country = _normalize_payment_component(billing_country).upper() or "US"
    currency = _normalize_payment_component(billing_currency).upper() or "USD"
    proxy_geo = _normalize_payment_component(proxy_geo_country).upper()
    locale_hint, timezone_hint = _PAYMENT_GEO_HINTS.get(country, ("en-US", "America/New_York"))
    if proxy_geo:
        proxy_geo_matches = proxy_geo == country
        proxy_geo_consistency = "match" if proxy_geo_matches else "mismatch"
        proxy_geo_consistency_reason = (
            "proxy_geo_matches_billing_country"
            if proxy_geo_matches
            else "proxy_geo_differs_from_billing_country"
        )
    else:
        proxy_geo_matches = None
        proxy_geo_consistency = "unknown"
        proxy_geo_consistency_reason = "proxy_geo_country_unavailable"
    return {
        "billing_country": country,
        "billing_currency": currency,
        "browser_locale_hint": locale_hint,
        "browser_timezone_hint": timezone_hint,
        "proxy_url": _normalize_payment_component(proxy_url),
        "proxy_geo_country": proxy_geo,
        "proxy_geo_matches_billing_country": proxy_geo_matches,
        "proxy_geo_consistency": proxy_geo_consistency,
        "proxy_geo_consistency_reason": proxy_geo_consistency_reason,
    }


def _load_account_extra(account) -> dict:
    try:
        import json as _json
        raw_extra = getattr(account, "extra_json", None) or getattr(account, "extra", None)
        if isinstance(raw_extra, str):
            return _json.loads(raw_extra) or {}
        if isinstance(raw_extra, dict):
            return raw_extra
    except Exception:
        pass
    return {}


def _persist_account_extra(task_id: str, account, update: dict, *, status: str | None = None) -> None:
    import json as _json

    merged = _load_account_extra(account)
    merged.update(update or {})
    account.extra_json = _json.dumps(merged, ensure_ascii=False)
    if status:
        account.status = status

    try:
        from core.db import Session as _DbSess, engine as _db_eng
        from core.db import AccountModel as _DbAcct
        from sqlmodel import select as _db_sel

        with _DbSess(_db_eng) as s:
            acct = s.exec(_db_sel(_DbAcct).where(_DbAcct.id == account.id)).first()
            if acct:
                raw = _load_account_extra(acct)
                raw.update(update or {})
                acct.extra_json = _json.dumps(raw, ensure_ascii=False)
                if status:
                    acct.status = status
                s.add(acct)
                s.commit()
                account.extra_json = acct.extra_json
                if status:
                    account.status = acct.status
    except Exception as db_err:
        _log(task_id, f"  [AutoPay] 写入 DB 失败（不影响支付结果）: {db_err}")


def _preflight_auto_pay_config(req: RegisterTaskRequest) -> None:
    if req.platform != "chatgpt":
        return

    from core.config_store import config_store
    cfg = config_store.get_all()
    merged_cfg = cfg.copy()
    merged_cfg.update({k: v for k, v in (req.extra or {}).items() if v is not None and v != ""})

    plan = _resolve_auto_pay_plan(req.extra, cfg)
    if plan not in ("plus", "team"):
        return

    try:
        from platforms.chatgpt.payment_auto import validate_payment_config
        validate_payment_config(merged_cfg)
    except Exception as exc:
        raise HTTPException(400, f"AutoPay config invalid: {exc}") from exc


@router.post("/register")
def create_register_task(
    req: RegisterTaskRequest,
    background_tasks: BackgroundTasks,
):
    _preflight_auto_pay_config(req)

    mail_provider = req.extra.get("mail_provider")
    if mail_provider == "luckmail":
        platform = req.platform
        if platform in ("tavily", "openblocklabs"):
            raise HTTPException(400, f"LuckMail 渠道暂时不支持 {platform} 项目注册")
        
        mapping = {
            "trae": "trae",
            "cursor": "cursor",
            "grok": "grok",
            "kiro": "kiro",
            "chatgpt": "openai"
        }
        req.extra["luckmail_project_code"] = mapping.get(platform, platform)

    task_id = f"task_{int(time.time()*1000)}"
    _create_task_record(task_id, req, "manual", req.extra)
    background_tasks.add_task(_run_register, task_id, req)
    return {"task_id": task_id}


@router.post("/{task_id}/stop")
def stop_task(task_id: str):
    with _tasks_lock:
        if task_id not in _tasks:
            raise HTTPException(404, "任务不存在")
    control = _task_store.request_stop(task_id)
    _log(task_id, "已收到停止任务请求")
    return {"ok": True, "control": control.get("control", {})}


@router.post("/{task_id}/pause")
def pause_task(task_id: str):
    with _tasks_lock:
        if task_id not in _tasks:
            raise HTTPException(404, "任务不存在")
    control = _task_store.request_pause(task_id)
    _log(task_id, "已收到暂停任务请求")
    return {"ok": True, "control": control.get("control", {})}


@router.post("/{task_id}/resume")
def resume_task(task_id: str):
    with _tasks_lock:
        if task_id not in _tasks:
            raise HTTPException(404, "任务不存在")
    control = _task_store.request_resume(task_id)
    _log(task_id, "已收到恢复任务请求")
    return {"ok": True, "control": control.get("control", {})}


@router.post("/{task_id}/skip-current")
def skip_current_task(task_id: str):
    with _tasks_lock:
        if task_id not in _tasks:
            raise HTTPException(404, "任务不存在")
    control = _task_store.request_skip_current(task_id)
    _log(task_id, "已收到跳过当前账号请求")
    return {"ok": True, "control": control.get("control", {})}


@router.get("/logs")
def get_logs(platform: str = None, page: int = 1, page_size: int = 50):
    with Session(engine) as s:
        q = select(TaskLog)
        if platform:
            q = q.where(TaskLog.platform == platform)
        q = q.order_by(TaskLog.id.desc())
        total = len(s.exec(q).all())
        items = s.exec(q.offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "items": items}


@router.post("/logs/batch-delete")
def batch_delete_logs(body: TaskLogBatchDeleteRequest):
    if not body.ids:
        raise HTTPException(400, "任务历史 ID 列表不能为空")

    unique_ids = list(dict.fromkeys(body.ids))
    if len(unique_ids) > 1000:
        raise HTTPException(400, "单次最多删除 1000 条任务历史")

    with Session(engine) as s:
        try:
            logs = s.exec(select(TaskLog).where(TaskLog.id.in_(unique_ids))).all()
            found_ids = {log.id for log in logs if log.id is not None}

            for log in logs:
                s.delete(log)

            s.commit()
            deleted_count = len(found_ids)
            not_found_ids = [log_id for log_id in unique_ids if log_id not in found_ids]
            logger.info("批量删除任务历史成功: %s 条", deleted_count)

            return {
                "deleted": deleted_count,
                "not_found": not_found_ids,
                "total_requested": len(unique_ids),
            }
        except Exception as e:
            s.rollback()
            logger.exception("批量删除任务历史失败")
            raise HTTPException(500, f"批量删除任务历史失败: {str(e)}")


def _read_progress_snapshot(task_id: str) -> tuple[dict, str] | tuple[None, str]:
    """读取任务摘要 + 轻量签名。锁内仅做引用拷贝以避免与注册线程长时间抢锁；
    JSON 序列化和昂贵的字段比较都放到锁外。
    返回 (snapshot_dict_or_None, signature)。
    """
    # 锁内：仅快速 get + 浅拷贝 list 引用
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return None, ""
        status = task.get("status", "")
        progress = task.get("progress", "")
        total = task.get("total", 0)
        started = task.get("started", 0)
        completed = task.get("completed", 0)
        success = task.get("success", 0)
        skipped = task.get("skipped", 0)
        errors = task.get("errors") or []
        errors_n = len(errors)
        # errors 为字符串 list，浅拷贝一次即可（避免锁外被并发追加导致越界）
        errors_copy = list(errors)
        error_msg = task.get("error", "")
        # worker_states：只取个数 + 最后一项的状态作为签名指纹（避免 200 次属性扫描）
        worker_states = task.get("worker_states") or []
        workers_n = len(worker_states)
        # 仅复制对外暴露用的引用列表（不深拷贝单个 dict；只读路径）
        worker_states_view = list(worker_states)
        # 用累计变更号作为 signature 一部分（如有则用，否则按首尾 stage 比对）
        change_id = int(task.get("worker_state_change_id") or 0)
        control = task.get("control") or {}
        control_view = dict(control)
        cashier_urls = task.get("cashier_urls") or []
        cashier_view = list(cashier_urls)
        platform = task.get("platform", "")

    snapshot = {
        "id": task_id,
        "status": status,
        "progress": progress,
        "total": total,
        "started": started,
        "completed": completed,
        "success": success,
        "skipped": skipped,
        "errors": errors_copy,
        "error": error_msg,
        "worker_states": worker_states_view,
        "control": control_view,
        "cashier_urls": cashier_view,
        "platform": platform,
    }
    # 轻量 signature：O(1) 字段拼接，不再扫描 worker_states 内部
    signature = (
        f"{status}|{progress}|{started}|{completed}|{success}|{skipped}"
        f"|{errors_n}|{workers_n}|{change_id}"
    )
    return snapshot, signature


@router.get("/{task_id}/progress/stream")
async def stream_progress(task_id: str):
    """SSE 任务摘要流：仅在状态变化时推送，事件驱动取代 2s 轮询。

    性能策略：
    - tick 间隔 1.5s（用户视觉无感，锁竞争降到原来 1/3）
    - 锁内仅复制引用，不做 JSON 序列化；签名 O(1) 计算
    - 仅在 signature 变化时才 dump JSON 并推送
    """
    with _tasks_lock:
        if task_id not in _tasks:
            raise HTTPException(404, "任务不存在")

    async def event_generator():
        last_signature: str = ""
        idle_ticks = 0
        first = True
        while True:
            if not first:
                await asyncio.sleep(1.5)
            first = False
            snap, signature = _read_progress_snapshot(task_id)
            if snap is None:
                yield f"data: {json.dumps({'gone': True}, ensure_ascii=False)}\n\n"
                break
            if signature != last_signature:
                last_signature = signature
                idle_ticks = 0
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            else:
                idle_ticks += 1
                # 每 ~15s 一次 keep-alive，防止反代超时
                if idle_ticks >= 10:
                    idle_ticks = 0
                    yield ": keep-alive\n\n"
            if snap.get("status") in ("done", "failed", "stopped"):
                yield f"data: {json.dumps({'final': True, **snap}, ensure_ascii=False)}\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{task_id}/logs/stream")
async def stream_logs(task_id: str, since: int = 0):
    """SSE 实时日志流"""
    with _tasks_lock:
        if task_id not in _tasks:
            raise HTTPException(404, "任务不存在")

    async def event_generator():
        sent = since
        while True:
            with _tasks_lock:
                task = _tasks.get(task_id, {})
                logs = list(task.get("logs", []))
                offset = int(task.get("log_offset", 0))
                total = int(task.get("log_total", offset + len(logs)))
                status = task.get("status", "")
            if sent < offset:
                yield f"data: {json.dumps({'reset': True, 'log_offset': offset, 'log_total': total})}\n\n"
                sent = offset
            while sent < total:
                local_index = sent - offset
                if local_index < 0 or local_index >= len(logs):
                    break
                yield f"data: {json.dumps({'line': logs[local_index], 'index': sent})}\n\n"
                sent += 1
            if status in ("done", "failed", "stopped"):
                yield f"data: {json.dumps({'done': True, 'status': status, 'log_offset': offset, 'log_total': total})}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )



# 定时任务管理 API
@router.post("/schedule/{task_id}/run")
def run_scheduled_task_now(task_id: str, background_tasks: BackgroundTasks):
    """立即执行定时任务"""
    from core.db import ScheduledTaskModel, Session, engine
    from core.scheduler import update_task_run_status
    from api.tasks import _run_register, _log
    import logging
    
    logger = logging.getLogger(__name__)
    
    with Session(engine) as s:
        task = s.get(ScheduledTaskModel, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        task_config = {
            "task_id": task.task_id,
            "platform": task.platform,
            "count": task.count,
            "executor_type": task.executor_type,
            "captcha_solver": task.captcha_solver,
            "extra": task.get_extra(),
            "interval_type": task.interval_type,
            "interval_value": task.interval_value,
        }
    run_task_id = f"manual_{task_id}_{int(time.time())}"
    
    # 创建 RegisterTaskRequest
    req = RegisterTaskRequest(**task_config)
    logger.info(f"准备手动运行任务 {run_task_id}, 配置：{task_config}")
    
    def run_with_status():
        try:
            # 先初始化 _tasks 记录
            _create_task_record(run_task_id, req, "scheduled_manual", req.extra)
            # 先记录开始
            _log(run_task_id, f"开始手动运行定时任务 {task_id}")
            _run_register(run_task_id, req)
            with _tasks_lock:
                task_state = dict(_tasks.get(run_task_id, {}))
            success = task_state.get("status") == "done" and not task_state.get("errors")
            error_msg = None if success else task_state.get("error") or "; ".join(task_state.get("errors") or [])
            update_task_run_status(task_id, success, error_msg)
            logger.info(f"任务 {run_task_id} 运行完成")
        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            update_task_run_status(task_id, False, error_msg)
            logger.error(f"任务 {run_task_id} 运行失败：{error_msg}")
    
    background_tasks.add_task(run_with_status)
    
    return {"task_id": run_task_id, "status": "running"}

@router.post("/schedule")
def create_scheduled_task(body: RegisterTaskRequest):
    """创建定时注册任务"""
    import uuid
    from core.db import ScheduledTaskModel, Session, engine
    from sqlmodel import select
    
    task_id = f"sched_{uuid.uuid4().hex[:8]}"
    
    # 保存到数据库
    db_task = ScheduledTaskModel(
        task_id=task_id,
        platform=body.platform,
        count=body.count,
        executor_type=body.executor_type,
        captcha_solver=body.captcha_solver,
        extra_json=json.dumps(body.extra, ensure_ascii=False),
        interval_type=body.interval_type or "minutes",
        interval_value=body.interval_value or 30,
        paused=False,
    )
    with Session(engine) as s:
        s.add(db_task)
        s.commit()
        s.refresh(db_task)
    
    # 添加到内存
    from core.scheduler import add_scheduled_register_task
    config = body.dict()
    config['task_id'] = task_id
    config['interval_type'] = db_task.interval_type
    config['interval_value'] = db_task.interval_value
    config['paused'] = db_task.paused
    add_scheduled_register_task(task_id, config)
    
    # 创建后立即在线程中执行一次
    def run_now():
        run_task_id = f"scheduled_{task_id}_{int(time.time())}"
        success = False
        error_msg = None
        try:
            # 先初始化 _tasks 记录
            req = RegisterTaskRequest(**config)
            _create_task_record(run_task_id, req, "scheduled", req.extra)
            _run_register(run_task_id, req)
            with _tasks_lock:
                task_state = dict(_tasks.get(run_task_id, {}))
            success = task_state.get("status") == "done" and not task_state.get("errors")
            if not success:
                error_msg = task_state.get("error") or "; ".join(task_state.get("errors") or [])
            print(f"[Scheduler] 任务 {task_id} 已执行", flush=True)
        except Exception as e:
            error_msg = str(e)
            print(f"[Scheduler] 任务 {task_id} 执行失败：{e}", flush=True)
        finally:
            # 更新任务运行状态
            from core.scheduler import update_task_run_status
            update_task_run_status(task_id, success, error_msg)
    
    threading.Thread(target=run_now, daemon=True).start()
    print(f"[Scheduler] 任务 {task_id} 已创建并启动", flush=True)
    
    return {"task_id": task_id, "status": "scheduled", "config": config}


@router.get("/schedule")
def list_scheduled_tasks():
    """获取所有定时任务"""
    from core.db import ScheduledTaskModel, Session, engine
    from core.scheduler import get_all_task_run_status
    run_status = get_all_task_run_status()

    result = []
    with Session(engine) as s:
        tasks = s.exec(select(ScheduledTaskModel)).all()
        for task in tasks:
            task_data = {
                "task_id": task.task_id,
                "platform": task.platform,
                "count": task.count,
                "executor_type": task.executor_type,
                "captcha_solver": task.captcha_solver,
                "extra": task.get_extra(),
                "interval_type": task.interval_type,
                "interval_value": task.interval_value,
                "paused": task.paused,
            }
            if task.task_id in run_status:
                task_data.update(run_status[task.task_id])
            else:
                task_data.setdefault("last_run_at", None)
                task_data.setdefault("last_run_success", None)
                task_data.setdefault("last_error", None)
            result.append(task_data)
    
    return {"tasks": result}


@router.get("/{task_id}")
def get_task(task_id: str, include_logs: bool = True):
    with _tasks_lock:
        if task_id not in _tasks:
            raise HTTPException(404, "任务不存在")
    item = _task_store.snapshot(task_id)
    if not include_logs:
        item.pop("logs", None)
    return item


@router.get("")
def list_tasks():
    with _tasks_lock:
        task_ids = list(_tasks.keys())
    summaries = []
    for task_id in task_ids:
        item = _task_store.snapshot(task_id)
        item.pop("logs", None)
        summaries.append(item)
    return summaries



# 定时任务管理 API - 更新任务
@router.put("/schedule")
def update_scheduled_task(body: RegisterTaskRequest):
    """更新定时任务配置"""
    from core.db import ScheduledTaskModel, Session, engine
    from core.scheduler import add_scheduled_register_task, remove_scheduled_register_task
    
    # 从根级别或 extra 中获取 task_id
    task_id = getattr(body, 'task_id', None) or (body.extra and body.extra.get('task_id'))
    if not task_id:
        raise HTTPException(400, "缺少任务 ID")
    
    config = body.dict()
    config['task_id'] = task_id
    with Session(engine) as s:
        task = s.get(ScheduledTaskModel, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        task.platform = body.platform
        task.count = body.count
        task.executor_type = body.executor_type
        task.captcha_solver = body.captcha_solver
        task.extra_json = json.dumps(body.extra, ensure_ascii=False)
        task.interval_type = body.interval_type or task.interval_type or "minutes"
        task.interval_value = body.interval_value or task.interval_value or 30
        task.updated_at = datetime.now(timezone.utc)
        s.add(task)
        s.commit()
        s.refresh(task)
        config['paused'] = task.paused
        config['interval_type'] = task.interval_type
        config['interval_value'] = task.interval_value
    if config.get('paused'):
        remove_scheduled_register_task(task_id)
    else:
        add_scheduled_register_task(task_id, config)
    
    return {"task_id": task_id, "status": "updated", "config": config}


# 手动运行定时任务



# 暂停/恢复定时任务



@router.delete("/schedule/{task_id}")
def delete_scheduled_task(task_id: str):
    """删除定时任务"""
    from core.db import ScheduledTaskModel, Session, engine
    from core.scheduler import remove_scheduled_register_task
    
    with Session(engine) as s:
        task = s.get(ScheduledTaskModel, task_id)
        if task:
            s.delete(task)
            s.commit()
    
    remove_scheduled_register_task(task_id)
    return {"ok": True}


@router.post("/schedule/{task_id}/toggle")
def toggle_scheduled_task(task_id: str):
    """暂停或恢复定时任务"""
    from core.db import ScheduledTaskModel, Session, engine
    from core.scheduler import add_scheduled_register_task, remove_scheduled_register_task
    
    with Session(engine) as s:
        task = s.get(ScheduledTaskModel, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        task.paused = not task.paused
        task.updated_at = datetime.now(timezone.utc)
        s.add(task)
        s.commit()
        s.refresh(task)
        paused = task.paused
        
        if paused:
            remove_scheduled_register_task(task_id)
        else:
            add_scheduled_register_task(task_id, {
                "task_id": task.task_id,
                "platform": task.platform,
                "count": task.count,
                "executor_type": task.executor_type,
                "captcha_solver": task.captcha_solver,
                "extra": task.get_extra(),
                "interval_type": task.interval_type,
                "interval_value": task.interval_value,
                "paused": task.paused,
            })
    
    return {"task_id": task_id, "paused": paused}
