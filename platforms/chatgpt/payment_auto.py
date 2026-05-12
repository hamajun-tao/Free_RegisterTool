"""
payment_auto.py — ChatGPT Plus/Team 自动 Stripe 支付引擎

设计原则：
  - 完全复用 Gpt-Agreement-Payment-main/CTF-pay/card.py 的成熟支付流程
  - 作为轻量适配层：把 auto_reg-main 的账号凭证 → card.py 所需 cfg dict
  - 对外只暴露一个函数 run_payment()，调用者无需了解内部细节
  - 不侵入任何现有注册逻辑

依赖：
  - requests（标准）
  - Gpt-Agreement-Payment-main/CTF-pay/card.py 以 subprocess 方式调用
    （避免 sys.path 污染和循环依赖）
"""

import json
import logging
import os
import re as _re_mod
import requests
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_COUNTRY_TO_CURRENCY = {
    "US": "USD", "GB": "GBP", "EU": "EUR", "IE": "EUR", "DE": "EUR", "FR": "EUR",
    "NL": "EUR", "ES": "EUR", "IT": "EUR", "SG": "SGD", "HK": "HKD", "JP": "JPY",
    "CA": "CAD", "IN": "INR", "BR": "BRL", "MX": "MXN",
}
_PAYPAL_EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
}
# ── Provider 定义 ────────────────────────────────────────────────────────────
# payment_provider 是路由键，payment_method 是传给 card.py 的底层方式
PROVIDER_PAYPAL_WEB = "paypal_web"
PROVIDER_GOPAY_API = "gopay_api"
PROVIDER_GOPAY_ANDROID = "gopay_android"
PROVIDER_MANUAL_LINK = "manual_link"
PROVIDER_CARD = "card"  # 传统信用卡

_PROVIDER_TO_METHOD = {
    PROVIDER_PAYPAL_WEB: "paypal",
    PROVIDER_GOPAY_API: "gopay",
    PROVIDER_GOPAY_ANDROID: "gopay",
    PROVIDER_CARD: "card",
    PROVIDER_MANUAL_LINK: "manual_link",
    # 向后兼容：老的 payment_method 值直接透传
    "paypal": "paypal",
    "gopay": "gopay",
    "card": "card",
}

_ANDROID_SETUP_KEYS = (
    "payment_android_avd_name",
    "payment_android_emulator_path",
    "payment_android_serial",
    "payment_android_adb_path",
    "payment_android_gopay_apk_path",
    "payment_android_gojek_apk_path",
)


def _has_android_emulator_setup(cfg: dict) -> bool:
    """检测是否配了任一模拟机必要字段（AVD / 设备 serial / APK / adb / emulator 路径）。

    任意一项非空即视为"想走模拟机路线"。
    """
    for k in _ANDROID_SETUP_KEYS:
        if str(cfg.get(k) or "").strip():
            return True
    return False


def resolve_provider(cfg: dict) -> tuple[str, str]:
    """从配置中解析 (provider, payment_method)。

    优先 payment_provider，向后兼容 payment_method；当 method=gopay 且检测到
    Android 模拟机必要字段时，自动路由到 gopay_android（否则走 gopay_api）。
    """
    raw_provider = str(cfg.get("payment_provider") or "").strip().lower()
    raw_method = str(cfg.get("payment_method") or "card").strip().lower()
    if raw_provider:
        provider = raw_provider
    elif raw_method == "paypal":
        provider = PROVIDER_PAYPAL_WEB
    elif raw_method == "gopay":
        provider = (
            PROVIDER_GOPAY_ANDROID if _has_android_emulator_setup(cfg) else PROVIDER_GOPAY_API
        )
    else:
        provider = raw_method or PROVIDER_CARD
    method = _PROVIDER_TO_METHOD.get(provider, provider)
    return provider, method

_PAYPAL_DEFAULT_BILLING = {
    "country": "IE",
    "currency": "EUR",
    "address": "1 Grand Canal Square",
    "city": "Dublin",
    "state": "",
    "zip": "D02 P820",
}

# 日本账单：触发 plus-1-month-free promo 的首选地区
_JP_DEFAULT_BILLING = {
    "country": "JP",
    "currency": "JPY",
    "address": "1-1-2 Marunouchi",
    "city": "Chiyoda-ku, Tokyo",
    "state": "Tokyo",
    "zip": "100-0005",
}

# PayPal 在这些国家可用（不止 EU），用于 promo + PayPal 共存场景
_PAYPAL_COMPATIBLE_COUNTRIES = _PAYPAL_EU_COUNTRIES | {
    "JP", "US", "CA", "AU", "NZ", "SG", "HK", "GB", "BR", "MX",
    "IN", "KR", "TW", "TH", "PH", "MY", "ID", "VN", "IL", "TR",
    "NO", "CH", "IS",
}

# Promo eligible 国家（首月免费大概率生效的 IP 地区）
_PROMO_ELIGIBLE_COUNTRIES = {
    "JP",  # 日本：几乎 100% 触发首月免费
    # 下面根据实测补充
}

# Stripe checkout 不支持 PayPal 的国家（payment_method_types 只返回 ['card']）
# 这些国家的 promo 仍然有效，但 billing 需改用 IE/EUR 才能走 PayPal
_PAYPAL_STRIPE_BLOCKED_BILLING = {
    "JP",  # 实测: JP billing → payment_method_types=['card'], IE billing → ['card','paypal'] 且 promo 仍 0 EUR
}

_PROMO_BILLING_DEFAULTS = {
    "JP": _JP_DEFAULT_BILLING,
    "IE": _PAYPAL_DEFAULT_BILLING,
}

_DEFAULT_CARD_PY_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "..",
        "Gpt-Agreement-Payment-main",
        "Gpt-Agreement-Payment-main",
        "CTF-pay",
        "card.py",
    )
)


def _configured_card_py_path() -> str:
    configured = os.environ.get("CARD_PY_PATH", "").strip()
    if configured:
        return os.path.normpath(os.path.expandvars(os.path.expanduser(configured)))
    try:
        from core.config_store import config_store
        configured = (config_store.get("payment_card_py_path") or "").strip()
    except Exception:
        configured = ""
    if configured:
        return os.path.normpath(os.path.expandvars(os.path.expanduser(configured)))
    return _DEFAULT_CARD_PY_PATH


class PaymentError(Exception):
    """支付流程错误（可恢复）"""


_PLACEHOLDER_SECRET_MARKERS = (
    "dummy",
    "placeholder",
    "your_",
    "your-",
    "example",
    "changeme",
)


def _looks_like_placeholder_secret(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _PLACEHOLDER_SECRET_MARKERS)


def _looks_like_placeholder_paypal_email(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {
        "pay@example.com",
        "paypal@example.com",
        "test@example.com",
        "payer@example.com",
    }


def _config_flag_enabled(value, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on", "enabled"}


def _payment_config_with_overrides(overrides: Optional[dict] = None) -> dict:
    from core.config_store import config_store
    cfg = config_store.get_all()
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None and v != ""})
    return cfg


def _first_config_value(cfg: dict, *keys: str) -> str:
    for key in keys:
        value = cfg.get(key)
        if value is None:
            value = os.environ.get(key, "")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _resolve_vlm_config(cfg: dict) -> dict:
    return {
        "base_url": _first_config_value(
            cfg,
            "payment_vlm_base_url",
            "CTF_VLM_BASE_URL",
            "ctf_vlm_base_url",
            "VLM_BASE_URL",
            "vlm_base_url",
        ),
        "api_key": _first_config_value(
            cfg,
            "payment_vlm_api_key",
            "CTF_VLM_API_KEY",
            "ctf_vlm_api_key",
            "VLM_API_KEY",
            "vlm_api_key",
        ),
        "model": _first_config_value(
            cfg,
            "payment_vlm_model",
            "CTF_VLM_MODEL",
            "ctf_vlm_model",
            "VLM_MODEL",
            "vlm_model",
        ) or "gpt-4o",
        "timeout_s": _first_config_value(
            cfg,
            "payment_vlm_timeout_s",
            "CTF_VLM_TIMEOUT_S",
            "ctf_vlm_timeout_s",
            "VLM_TIMEOUT_S",
            "vlm_timeout_s",
        ) or "45",
    }


def _validate_captcha_key_online(cfg: dict) -> None:
    api_url = (cfg.get("payment_captcha_api_url") or "").strip().rstrip("/")
    captcha_key = (cfg.get("payment_captcha_key") or "").strip()
    if not api_url or not captcha_key:
        return

    try:
        resp = requests.post(f"{api_url}/getBalance", json={"clientKey": captcha_key}, timeout=15)
        data = resp.json()
    except Exception as exc:
        raise PaymentError(f"payment_captcha_key online validation failed: {exc}") from exc

    if data.get("errorId", 0) != 0:
        code = data.get("errorCode", "UNKNOWN_ERROR")
        desc = data.get("errorDescription", "")
        raise PaymentError(f"payment_captcha_key rejected by provider: {code} {desc}".strip())


def _check_payment_solver_runtime(cfg: dict) -> None:
    vlm_cfg = _resolve_vlm_config(cfg)
    if not (vlm_cfg.get("base_url") and vlm_cfg.get("api_key")):
        return
    payment_python = _resolve_payment_python_executable((cfg.get("payment_python_executable") or "").strip())
    if not os.path.isfile(payment_python):
        raise PaymentError(f"payment_python_executable does not exist: {payment_python}")
    required = ["cv2", "numpy", "torch", "PIL", "playwright", "transformers", "requests"]
    probe = (
        "import importlib.util, json; "
        f"mods={required!r}; "
        "missing=[]; "
        "[missing.append(m) for m in mods if not _importlib_import(m)]; "
        "print(json.dumps({'missing': missing}))"
    ).replace("_importlib_import", "importlib.util.find_spec")
    try:
        proc = subprocess.run(
            [payment_python, "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        raise PaymentError(f"hCaptcha VLM solver runtime check failed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise PaymentError(f"hCaptcha VLM solver runtime check failed under {payment_python}: {detail}")
    try:
        data = json.loads((proc.stdout or "{}").strip().splitlines()[-1])
    except Exception as exc:
        raise PaymentError(f"hCaptcha VLM solver runtime check returned invalid output: {(proc.stdout or '')[:200]}") from exc
    missing = data.get("missing") or []
    if missing:
        raise PaymentError(
            "hCaptcha VLM solver runtime missing modules under "
            f"{payment_python}: {', '.join(missing)}. "
            f"Install them in that runtime before AutoPay, e.g. {payment_python} -m pip install opencv-python numpy torch pillow playwright transformers requests"
        )


def _looks_like_placeholder_card_number(value: str) -> bool:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits in {
        "4242424242424242",
        "4000000000000002",
        "4000000000009995",
        "5555555555554444",
    }


def _card_number_luhn_valid(value: str) -> bool:
    digits = [int(ch) for ch in str(value or "") if ch.isdigit()]
    if len(digits) < 12 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def validate_payment_config(cfg: dict, *, require_captcha_key: bool = True) -> None:
    """Fail fast for AutoPay config values that would waste a full checkout run."""
    payment_method = (cfg.get("payment_method") or "card").strip().lower()
    if payment_method not in {"card", "paypal", "gopay"}:
        raise PaymentError(f"unsupported payment_method: {payment_method}")
    gopay_auto_register = _config_flag_enabled(cfg.get("payment_gopay_auto_register"), default=False)
    configured_card_py = (cfg.get("payment_card_py_path") or os.environ.get("CARD_PY_PATH") or "").strip()
    if configured_card_py:
        card_py = os.path.normpath(os.path.expandvars(os.path.expanduser(configured_card_py)))
        if not os.path.isfile(card_py):
            raise PaymentError(f"payment_card_py_path does not exist: {card_py}")
    elif not _find_card_py():
        raise PaymentError(
            "card.py not found; set payment_card_py_path or CARD_PY_PATH before enabling AutoPay."
        )
    configured_python = (cfg.get("payment_python_executable") or "").strip()
    if configured_python:
        payment_python = _resolve_payment_python_executable(configured_python)
        if not os.path.isfile(payment_python):
            raise PaymentError(
                f"payment_python_executable does not exist: {payment_python}"
            )

    if payment_method == "card":
        card_number = (cfg.get("payment_card_number") or "").strip()
        if _looks_like_placeholder_card_number(card_number):
            raise PaymentError(
                "payment_method=card but payment_card_number is a test/placeholder card; set a real card before enabling AutoPay."
            )
        if card_number and not _card_number_luhn_valid(card_number):
            raise PaymentError(
                "payment_method=card but payment_card_number failed Luhn validation; check the configured card number."
            )
        missing_card_fields = [
            key for key in (
                "payment_card_number",
                "payment_card_cvc",
                "payment_card_exp_month",
                "payment_card_exp_year",
            )
            if not (cfg.get(key) or "").strip()
        ]
        if missing_card_fields:
            raise PaymentError(
                "payment_method=card but card config is incomplete: "
                f"{', '.join(missing_card_fields)}"
            )
    if payment_method == "paypal":
        paypal_email = (cfg.get("payment_paypal_email") or "").strip()
        paypal_password = (cfg.get("payment_paypal_password") or "").strip()
        if not paypal_email:
            raise PaymentError("payment_method=paypal but payment_paypal_email is empty.")
        if _looks_like_placeholder_paypal_email(paypal_email):
            raise PaymentError(
                f"payment_paypal_email is a placeholder value ({paypal_email}); "
                "set the real PayPal account before enabling AutoPay."
            )
        if not paypal_password:
            raise PaymentError("payment_method=paypal but payment_paypal_password is empty.")
        if _looks_like_placeholder_secret(paypal_password):
            raise PaymentError(
                "payment_paypal_password is a placeholder value; "
                "set the real PayPal password before enabling AutoPay."
            )
    if payment_method == "gopay":
        if gopay_auto_register:
            if not (cfg.get("smsbower_api_key") or "").strip():
                raise PaymentError(
                    "payment_method=gopay and payment_gopay_auto_register=1 but smsbower_api_key is empty."
                )
        elif not (cfg.get("payment_gopay_phone") or "").strip():
            raise PaymentError("payment_method=gopay but payment_gopay_phone is empty.")
        if not gopay_auto_register and not (cfg.get("payment_gopay_pin") or "").strip():
            raise PaymentError("payment_method=gopay but payment_gopay_pin is empty.")

    captcha_key = (cfg.get("payment_captcha_key") or "").strip()
    if require_captcha_key and not captcha_key:
        raise PaymentError(
            "payment_captcha_key is empty; set a real captcha platform key or disable AutoPay."
        )
    if _looks_like_placeholder_secret(captcha_key):
        raise PaymentError(
            f"payment_captcha_key is a placeholder value ({captcha_key}); "
            "set a real captcha platform key or disable AutoPay."
        )
    if _config_flag_enabled(cfg.get("payment_captcha_validate_online"), default=False):
        _validate_captcha_key_online(cfg)
    _check_payment_solver_runtime(cfg)


class PaymentResult:
    def __init__(
        self,
        *,
        success: bool,
        state: str,
        error: str = "",
        receipt_url: str = "",
        stage: str = "",
        diagnostic_code: str = "",
        provider: str = "",
        retryable: bool = False,
    ):
        self.success = success
        self.state = state
        self.error = error
        self.receipt_url = receipt_url
        self.stage = stage
        self.diagnostic_code = diagnostic_code
        self.provider = provider
        self.retryable = retryable

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "state": self.state,
            "error": self.error,
            "receipt_url": self.receipt_url,
        }
        if self.stage:
            d["stage"] = self.stage
        if self.diagnostic_code:
            d["diagnostic_code"] = self.diagnostic_code
        if self.provider:
            d["provider"] = self.provider
        if self.retryable:
            d["retryable"] = True
        return d


# ── stdout 阶段标记 → 诊断码映射 ──────────────────────────────────────────
# card.py 把关键事件输出到 stdout，我们从中提取精确的失败阶段。
# 顺序重要：靠前的优先匹配。
_STAGE_MARKERS: list[tuple[str, str, str, bool]] = [
    # (stdout 关键词, diagnostic_code, stage, retryable)
    # ── 服务端风控拦截（不可重试，换 IP/策略也没用）──
    ("result.*blocked",                     "approve_blocked",           "approve",           False),
    ("approve.*blocked",                    "approve_blocked",           "approve",           False),
    ("manual_approval.*blocked",            "approve_blocked",           "approve",           False),
    # ── DataDome / hCaptcha ──
    ("CARD_DATADOME_SLIDER=1",             "datadome_slider",           "paypal_datadome",   True),
    ("DataDome 可见滑块，放弃当前 IP",       "datadome_ip_blocked",       "paypal_datadome",   True),
    ("DataDome 滑块 solver 失败",           "datadome_slider_failed",    "paypal_datadome",   True),
    ("no hcaptcha frames",                  "hcaptcha_no_frame",         "paypal_hcaptcha",   True),
    ("hCaptcha 安全检查超时",               "hcaptcha_timeout",          "paypal_hcaptcha",   True),
    ("hCaptcha 解题失败",                   "hcaptcha_failed",           "paypal_hcaptcha",   True),
    ("PayPal hCaptcha 解题失败",            "hcaptcha_paypal_failed",    "paypal_hcaptcha",   True),
    ("未配置验证码 API key",                "captcha_key_missing",       "paypal_hcaptcha",   False),
    # ── PayPal 流程 ──
    ("hermes 参数缺失",                     "hermes_params_missing",     "paypal_hermes",     True),
    ("hermes 失败",                         "hermes_http_failed",        "paypal_hermes",     True),
    ("未捕获到 pm-redirects",               "paypal_callback_timeout",   "paypal_callback",   True),
    ("PayPal 浏览器授权失败",               "paypal_browser_auth",       "paypal_auth",       True),
    ("consent 按钮",                        "paypal_consent_missing",    "paypal_consent",    True),
    # ── Checkout / Stripe ──
    ("FreshCheckoutAuthError",              "checkout_auth_error",       "checkout_create",   False),
    ("fresh_checkout_session 失败",         "checkout_session_failed",   "checkout_create",   False),
    ("[400]",                               "checkout_400",              "checkout_create",   False),
    ("[404]",                               "checkout_404",              "checkout_create",   False),
    ("[403]",                               "checkout_403",              "checkout_create",   False),
    ("declined",                            "card_declined",             "stripe_confirm",    False),
    ("insufficient_funds",                  "card_insufficient",         "stripe_confirm",    False),
    # ── GoPay ──
    ("gopay.*OTP.*超时",                    "gopay_otp_timeout",         "gopay_otp",         True),
    ("gopay.*PIN.*失败",                    "gopay_pin_failed",          "gopay_pin",         True),
    ("gopay.*linking.*失败",                "gopay_linking_failed",      "gopay_linking",     True),
    ("Midtrans.*失败",                      "gopay_midtrans_failed",     "gopay_linking",     True),
    ("支付已落到终态失败",                   "terminal_failure",          "stripe_terminal",   False),
]


def _extract_diagnostic_from_stdout(lines: list[str], payment_method: str = "") -> dict:
    """从 card.py stdout 行中提取最精确的诊断信息。

    Returns dict with keys: diagnostic_code, stage, retryable
    """
    full_text = "\n".join(lines[-200:])  # 只看最后 200 行，避免过早匹配
    for marker, diag_code, stage, retryable in _STAGE_MARKERS:
        # 支持简单正则（含 .* 的视为 regex）
        if ".*" in marker or "[" in marker:
            if _re_mod.search(marker, full_text, _re_mod.IGNORECASE):
                return {"diagnostic_code": diag_code, "stage": stage, "retryable": retryable}
        else:
            if marker in full_text:
                return {"diagnostic_code": diag_code, "stage": stage, "retryable": retryable}
    # 未匹配到具体标记时，根据 payment_method 给一个泛化诊断
    if payment_method == "paypal":
        return {"diagnostic_code": "paypal_unknown", "stage": "paypal_auth", "retryable": True}
    if payment_method == "gopay":
        return {"diagnostic_code": "gopay_unknown", "stage": "gopay_pay", "retryable": True}
    return {"diagnostic_code": "unknown", "stage": "unknown", "retryable": False}


# ═══════════════════════════════════════════════════════════════════════════
# PayPal 预热登录：用户手动完成首次登录（含 OTP），保存 session
# ═══════════════════════════════════════════════════════════════════════════

_PAYPAL_PERSIST_DIR = os.path.join(
    os.path.dirname(_DEFAULT_CARD_PY_PATH),  # CTF-pay/
    "paypal_cf_persist",
)

_PAYPAL_COOKIE_FILE = os.path.join(_PAYPAL_PERSIST_DIR, "paypal_cookies.json")


def paypal_prewarm_login(
    email: str = "",
    password: str = "",
    proxy_url: str = "",
    timeout: int = 300,
    log_fn=None,
) -> dict:
    """
    用 Camoufox（反指纹浏览器）打开 PayPal 登录页面，用户手动完成登录 + OTP。

    流程：
    1. 启动 Camoufox（headful，持久化 profile，和 card.py 同款反指纹）
    2. 自动填入 email/password（如果提供）
    3. 等待用户手动完成 OTP 或其他风控验证
    4. 检测登录成功后保存 cookie
    5. 持久化 profile 保留 session，card.py 后续复用

    返回 {"success": bool, "message": str, "cookies_saved": int}
    """
    _log_fn = log_fn or (lambda msg: print(msg))

    os.makedirs(_PAYPAL_PERSIST_DIR, exist_ok=True)
    prewarm_profile = os.path.join(_PAYPAL_PERSIST_DIR, "prewarm_camoufox")
    os.makedirs(prewarm_profile, exist_ok=True)

    _log_fn("[PayPal PreWarm] 启动 Camoufox 反指纹浏览器...")

    try:
        from camoufox.sync_api import Camoufox
        from browserforge.fingerprints import Screen
    except ImportError:
        return {"success": False, "message": "camoufox 未安装，请: pip install camoufox", "cookies_saved": 0}

    cf_proxy = None
    if proxy_url:
        from urllib.parse import urlparse
        p = urlparse(proxy_url)
        cf_proxy = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username:
            cf_proxy["username"] = p.username
            cf_proxy["password"] = p.password or ""

    try:
        with Camoufox(
            headless=False,
            humanize=False,
            persistent_context=True,
            user_data_dir=prewarm_profile,
            os="windows",
            screen=Screen(max_width=1920, max_height=1080),
            proxy=cf_proxy,
            geoip=False,
            locale="zh-CN",
        ) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            # 导航到 PayPal 登录页
            _log_fn("[PayPal PreWarm] 打开 PayPal 登录页...")
            page.goto("https://www.paypal.com/signin", wait_until="domcontentloaded", timeout=30000)

            # 自动填入 email
            if email:
                try:
                    email_input = page.wait_for_selector("#email", timeout=5000)
                    if email_input:
                        email_input.fill(email)
                        _log_fn(f"[PayPal PreWarm] 已填入邮箱: {email}")
                        next_btn = page.query_selector("#btnNext")
                        if next_btn:
                            next_btn.click()
                            page.wait_for_timeout(2000)
                            if password:
                                pwd_input = page.query_selector("#password")
                                if pwd_input:
                                    pwd_input.fill(password)
                                    _log_fn("[PayPal PreWarm] 已填入密码，请检查后点击登录")
                except Exception as e:
                    _log_fn(f"[PayPal PreWarm] 自动填入失败（可手动操作）: {e}")

            _log_fn(f"[PayPal PreWarm] 等待登录完成（最多 {timeout}s）...")
            _log_fn("[PayPal PreWarm] 如果需要验证码，请在浏览器中手动完成")

            start_time = time.time()
            logged_in = False
            while time.time() - start_time < timeout:
                try:
                    current_url = page.url
                    if any(kw in current_url for kw in ["/myaccount", "/summary", "/activities"]):
                        logged_in = True
                        _log_fn("[PayPal PreWarm] ✅ 检测到登录成功！")
                        break
                    title = page.title()
                    if any(kw in title.lower() for kw in ["my paypal", "我的paypal", "dashboard", "活动"]):
                        logged_in = True
                        _log_fn("[PayPal PreWarm] ✅ 检测到登录成功（标题匹配）！")
                        break
                except Exception:
                    pass
                time.sleep(2)

            if not logged_in:
                _log_fn("[PayPal PreWarm] ⏰ 等待超时，未检测到登录成功")
                return {"success": False, "message": "登录超时", "cookies_saved": 0}

            # 保存 cookies 到 JSON（备用）
            cookies = ctx.cookies()
            paypal_cookies = [c for c in cookies if "paypal.com" in c.get("domain", "")]
            with open(_PAYPAL_COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(paypal_cookies, f, ensure_ascii=False, indent=2)

            _log_fn(f"[PayPal PreWarm] 💾 已保存 {len(paypal_cookies)} 个 cookie")
            _log_fn(f"[PayPal PreWarm] 持久化 profile: {prewarm_profile}")

            return {
                "success": True,
                "message": f"登录成功，{len(paypal_cookies)} 个 cookie 已保存到持久化 profile",
                "cookies_saved": len(paypal_cookies),
            }

    except Exception as e:
        _log_fn(f"[PayPal PreWarm] 错误: {e}")
        return {"success": False, "message": str(e), "cookies_saved": 0}


def _resolve_payment_python_executable(configured_path: str = "") -> str:
    """Resolve the Python executable used to spawn external CTF-pay runtime."""
    candidate = str(configured_path or "").strip()
    if candidate:
        return os.path.normpath(os.path.expandvars(os.path.expanduser(candidate)))
    return sys.executable


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Terminate the external payment runtime and any browsers/solver children it spawned."""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def _normalize_country_code(value: str) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if len(normalized) == 2 and normalized.isalpha() else ""


def _resolve_browser_locale_country(
    *,
    billing_country: str,
    proxy_geo_country: str = "",
) -> str:
    """返回 card.py LOCALE_PROFILES 的 locale key。

    优先使用代理地理位置（浏览器指纹应与 IP 一致），
    PayPal 场景下尤其关键（PayPal 对地理分叉敏感）。
    代理未知时回退到账单国家。
    """
    proxy_geo = _normalize_country_code(proxy_geo_country)
    if proxy_geo:
        return proxy_geo
    return _normalize_country_code(billing_country) or "US"


def _build_card_cfg(
    *,
    plan_name: str,
    access_token: str,
    session_token: str,
    cookie_header: str,
    device_id: str,
    card_number: str,
    card_cvc: str,
    card_exp_month: str,
    card_exp_year: str,
    billing_name: str,
    billing_country: str,
    billing_address: str,
    billing_city: str,
    billing_state: str,
    billing_zip: str,
    billing_currency: str,
    team_workspace_name: str,
    team_seat_quantity: int,
    captcha_api_url: str,
    captcha_key: str,
    paypal_email: str = "",
    paypal_password: str = "",
    gopay_phone: str = "",
    gopay_pin: str = "",
    gopay_otp_file: str = "",
    gopay_otp_url: str = "",
    proxy_url: str = "",
    browser_locale_country: str = "",
    skip_if_not_free: bool = False,
    auto_cancel_after_subscribe: bool = True,
    is_coupon_from_query_param: bool = False,
    checkout_ui_mode: str = "custom",
    vlm_base_url: str = "",
    vlm_api_key: str = "",
    vlm_model: str = "",
    vlm_timeout_s: str = "45",
) -> dict:
    """
    把 auto_reg-main 的账号凭证 + 配置转成 card.py 期望的 cfg dict 结构。
    使用 fresh_checkout 模式（access_token 直接生成 checkout），无需 flows 文件。
    """
    is_plus = "plus" in plan_name.lower()

    cfg = {
        # ── 代理 ──────────────────────────────────────────────────────────
        # card.py 只识别字符串代理，或 {host, port, user, pass} 结构。
        # 这里传字符串，避免它把 {"http": ..., "https": ...} 当成空代理处理。
        "proxy": proxy_url or None,
        "locale": (browser_locale_country or "").upper(),
        # 新注册账号 free trial 检查：promo 未生效时跳过支付，账号保留 free
        "skip_if_not_free": bool(skip_if_not_free),
        # 开通后立即取消订阅以防下月自动扣费（plus-1-month-free 试用核心保护）
        "auto_cancel_after_subscribe": bool(auto_cancel_after_subscribe),

        # ── 信用卡信息 ─────────────────────────────────────────────────────
        "cards": [
            {
                "number": card_number,
                "cvc": card_cvc,
                "exp_month": card_exp_month,
                "exp_year": card_exp_year,
                "email": "",           # 由 access_token 推导
                "name": billing_name,
                # CTF-pay/card.py submits tax/address data from cards[0].address.
                "address": {
                    "country": billing_country.upper(),
                    "line1": billing_address,
                    "line2": "",
                    "city": billing_city,
                    "state": billing_state,
                    "postal_code": billing_zip,
                },
            }
        ],

        # ── 账单地址 ──────────────────────────────────────────────────────
        "billing": {
            "name":          billing_name,
            "email":         "",       # 由 access_token 推导
            "country":       billing_country.upper(),
            "currency":      billing_currency.upper(),
            "address_line1": billing_address,
            "address_line2": "",
            "address_city":  billing_city,
            "address_state": billing_state,
            "line1":         billing_address,
            "line2":         "",
            "city":          billing_city,
            "state":         billing_state,
            "postal_code":   billing_zip,
        },

        # ── 套餐 ───────────────────────────────────────────────────────────
        "team_plan": {
            "plan_name":       plan_name,
            "workspace_name":  team_workspace_name or "MyWorkspace",
            "price_interval":  "month",
            "seat_quantity":   team_seat_quantity,
            # 首月免费促销（不存在则后端忽略）
            "promo_campaign_id": "plus-1-month-free" if is_plus else "team-1-month-free",
        },

        # ── hCaptcha 打码 ──────────────────────────────────────────────────
        "captcha": {
            "api_url":    captcha_api_url,
            "api_key":    captcha_key,
            "client_key": captcha_key,
        },

        # ── Fresh Checkout 配置（核心）────────────────────────────────────
        "fresh_checkout": {
            "enabled": True,
            # 不走 flows 文件，直接用 access_token 生成 checkout
            "bootstrap_from_flows": False,
            "request_style": "auto",
            "fallback_to_modern": True,
            "warmup_chatgpt_context": True,
            "warmup_home_bounce": False,  # 不访问主页（减少特征）
            "warmup_route_data":  True,

            "auth": {
                "mode":          "access_token",
                "access_token":  access_token,
                "session_token": session_token,
                "cookie_header": cookie_header,
                "device_id":     device_id,
            },

            "plan": {
                "plan_name":       plan_name,
                "billing_country": billing_country.upper(),
                "billing_currency": billing_currency.upper(),
                "workspace_name":  team_workspace_name or "MyWorkspace",
                "seat_quantity":   team_seat_quantity,
                "price_interval":  "month",
                "promo_campaign_id": "plus-1-month-free" if is_plus else "team-1-month-free",
                # 关键：is_coupon_from_query_param=False 可显著提升 promo eligible 率
                # （用户示例 JS 代码证实 false 时 OpenAI 判定更宽松）
                "is_coupon_from_query_param": bool(is_coupon_from_query_param),
                # checkout_ui_mode: "custom"（默认，嵌入式）/ "hosted"（Stripe 托管页）
                "checkout_ui_mode": str(checkout_ui_mode or "custom"),
            },

            # Fresh checkout 时不用 proxy 覆盖（跟随顶层代理）
        },
    }

    vlm_base_url = str(vlm_base_url or "").strip()
    vlm_api_key = str(vlm_api_key or "").strip()
    vlm_model = str(vlm_model or "gpt-4o").strip()
    if vlm_base_url and vlm_api_key:
        try:
            vlm_timeout = int(str(vlm_timeout_s or "45").strip())
        except Exception:
            vlm_timeout = 45
        _locale_key = (browser_locale_country or billing_country or "US").upper()
        _locale_profiles = {
            "US": ("en-US", "America/Chicago"),
            "JP": ("ja-JP", "Asia/Tokyo"),
            "DE": ("de-DE", "Europe/Berlin"),
            "IE": ("en-IE", "Europe/Dublin"),
            "FR": ("fr-FR", "Europe/Paris"),
            "NL": ("nl-NL", "Europe/Amsterdam"),
            "SG": ("en-SG", "Asia/Singapore"),
            "HK": ("zh-HK", "Asia/Hong_Kong"),
            "GB": ("en-GB", "Europe/London"),
            "AU": ("en-AU", "Australia/Sydney"),
            "CA": ("en-CA", "America/Toronto"),
            "BR": ("pt-BR", "America/Sao_Paulo"),
            "IN": ("en-IN", "Asia/Kolkata"),
            "KR": ("ko-KR", "Asia/Seoul"),
            "IT": ("it-IT", "Europe/Rome"),
            "MX": ("es-MX", "America/Mexico_City"),
        }
        _browser_locale, _browser_timezone = _locale_profiles.get(_locale_key, _locale_profiles["US"])
        _browser_lang_short = _browser_locale.split("-", 1)[0]
        cfg["browser_challenge"] = {
            "enabled": True,
            "auto_launch": True,
            "headless": False,
            "timeout_ms": 90000,
            "browser_locale": _browser_locale,
            "browser_timezone": _browser_timezone,
            "accept_language": f"{_browser_locale},{_browser_lang_short};q=0.9",
            "external_solver": {
                "enabled": True,
                "timeout_s": vlm_timeout + 30,
                "headed": True,
                "vlm": {
                    "enabled": True,
                    "base_url": vlm_base_url,
                    "api_key": vlm_api_key,
                    "model": vlm_model,
                    "timeout_s": vlm_timeout,
                }
            },
        }
    
    if paypal_email and paypal_password:
        # 当 promo 代理激活时，禁止 card.py 内部的 auto_eu_billing
        # 覆盖我们已对齐的 billing（否则 JP IP + IE billing → blocked）
        _promo_active = bool(os.environ.get("_PAYMENT_PROMO_PROXY_ACTIVE"))
        cfg["paypal"] = {
            "email": paypal_email,
            "password": paypal_password,
            "auto_eu_billing": not _promo_active,
        }
        
    if gopay_phone and gopay_pin:
        # 从手机号自动推断 country_code（支持 +86, +62, +60 等任意国家）
        # 兼容格式：+86 158..., 8615..., 158... (无国家码默认印尼62)
        import re as _re
        _phone_clean = _re.sub(r"\D", "", str(gopay_phone))
        _country_code = "62"  # 默认印尼
        _phone_local = _phone_clean
        if str(gopay_phone).strip().startswith("+"):
            # 显式 +XX 格式：根据常见国家码长度匹配
            for _cc in ("86", "62", "60", "65", "63", "66", "84", "91", "1", "44", "81", "82", "852", "886"):
                if _phone_clean.startswith(_cc):
                    _country_code = _cc
                    _phone_local = _phone_clean[len(_cc):]
                    break
        elif _phone_clean.startswith("86") and len(_phone_clean) >= 13:
            # 86 1XXXXXXXXXX (中国手机号 11 位)
            _country_code = "86"
            _phone_local = _phone_clean[2:]
        elif _phone_clean.startswith("62") and len(_phone_clean) >= 11:
            _country_code = "62"
            _phone_local = _phone_clean[2:]

        cfg["gopay"] = {
            "country_code": _country_code,
            "phone_number": _phone_local,
            "pin": gopay_pin
        }
        if gopay_otp_url:
            cfg["gopay"]["otp"] = {
                "source": "http",
                "url": gopay_otp_url
            }
        elif gopay_otp_file:
            cfg["gopay"]["otp"] = {
                "source": "file",
                "path": gopay_otp_file
            }

    return cfg


def _find_card_py() -> Optional[str]:
    """定位 card.py 文件路径（多候选路径策略）"""
    candidates = [
        _configured_card_py_path(),
        os.path.join(os.path.dirname(__file__), "..", "..", "CTF-pay", "card.py"),
    ]
    for p in candidates:
        normalized = os.path.normpath(p)
        if os.path.isfile(normalized):
            return normalized
    return None


def run_payment(
    *,
    plan_name: str,                 # "chatgptplusplan" 或 "chatgptteamplan"
    access_token: str,
    payment_method: str = "card",   # "card" / "gopay" / "paypal"
    session_token: str = "",
    cookie_header: str = "",
    device_id: str = "",
    card_number: str = "",
    card_cvc: str = "",
    card_exp_month: str = "",
    card_exp_year: str = "",
    billing_name: str = "",
    billing_country: str = "US",
    billing_address: str = "",
    billing_city: str = "",
    billing_state: str = "",
    billing_zip: str = "",
    billing_currency: str = "USD",
    team_workspace_name: str = "MyWorkspace",
    team_seat_quantity: int = 5,
    captcha_api_url: str = "",
    captcha_key: str = "",
    paypal_email: str = "",
    paypal_password: str = "",
    gopay_phone: str = "",
    gopay_pin: str = "",
    gopay_otp_file: str = "",
    gopay_otp_url: str = "",
    proxy_url: str = "",
    proxy_geo_country: str = "",
    timeout: int = 180,
    python_executable: str = "",
    skip_if_not_free: bool = False,
    auto_cancel_after_subscribe: bool = True,
    is_coupon_from_query_param: bool = False,
    checkout_ui_mode: str = "custom",
    vlm_base_url: str = "",
    vlm_api_key: str = "",
    vlm_model: str = "",
    vlm_timeout_s: str = "45",
    log_fn=None,
) -> PaymentResult:
    """
    执行完整的 Stripe 支付流程。

    实现策略：把配置写成临时 JSON 文件，以 subprocess 调用 card.py --json-result，
    解析 stdout 中的 CARD_RESULT_JSON=... 行获取结果。

    优点：
      - card.py 运行在独立进程，崩溃不影响主进程
      - 无 sys.path 污染
      - 日志由 card.py 自行输出到文件，不干扰主进程日志
    """
    _log = log_fn or (lambda msg: logger.info("[payment] %s", msg))
    payment_method = str(payment_method or "card").strip().lower()
    if payment_method not in {"card", "paypal", "gopay"}:
        raise PaymentError(f"run_payment: unsupported payment_method: {payment_method}")

    if not (access_token or session_token or cookie_header):
        raise PaymentError("run_payment: access_token/session_token/cookie_header 不能同时为空")
    if payment_method == "card":
        missing_card_fields = [
            name for name, value in (
                ("card_number", card_number),
                ("card_cvc", card_cvc),
                ("card_exp_month", card_exp_month),
                ("card_exp_year", card_exp_year),
            )
            if not str(value or "").strip()
        ]
        if missing_card_fields:
            raise PaymentError(f"run_payment: 信用卡配置不完整: {', '.join(missing_card_fields)}")

    card_py = _find_card_py()
    if not card_py:
        raise PaymentError(
            f"找不到 card.py，请检查 CARD_PY_PATH 环境变量或文件是否存在。"
            f" 当前查找路径: {_configured_card_py_path()}"
        )

    cfg = _build_card_cfg(
        plan_name=plan_name,
        access_token=access_token,
        session_token=session_token,
        cookie_header=cookie_header,
        device_id=device_id,
        card_number=card_number,
        card_cvc=card_cvc,
        card_exp_month=card_exp_month,
        card_exp_year=card_exp_year,
        billing_name=billing_name,
        billing_country=billing_country,
        billing_address=billing_address,
        billing_city=billing_city,
        billing_state=billing_state,
        billing_zip=billing_zip,
        billing_currency=billing_currency,
        team_workspace_name=team_workspace_name,
        team_seat_quantity=team_seat_quantity,
        captcha_api_url=captcha_api_url,
        captcha_key=captcha_key,
        paypal_email=paypal_email,
        paypal_password=paypal_password,
        gopay_phone=gopay_phone,
        gopay_pin=gopay_pin,
        gopay_otp_file=gopay_otp_file,
        gopay_otp_url=gopay_otp_url,
        proxy_url=proxy_url,
        browser_locale_country=_resolve_browser_locale_country(
            billing_country=billing_country,
            proxy_geo_country=proxy_geo_country,
        ),
        skip_if_not_free=skip_if_not_free,
        auto_cancel_after_subscribe=auto_cancel_after_subscribe,
        is_coupon_from_query_param=is_coupon_from_query_param,
        checkout_ui_mode=checkout_ui_mode,
        vlm_base_url=vlm_base_url,
        vlm_api_key=vlm_api_key,
        vlm_model=vlm_model,
        vlm_timeout_s=vlm_timeout_s,
    )

    # 写入临时配置文件
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8", delete=False
    ) as tf:
        json.dump(cfg, tf, ensure_ascii=False)
        cfg_path = tf.name

    try:
        payment_python = _resolve_payment_python_executable(python_executable)
        _log(f"启动支付流程 plan={plan_name} config={cfg_path}")
        cmd = [
            payment_python, card_py,
            "fresh",               # 使用 fresh checkout 模式
            "--config", cfg_path,
            "--json-result",       # 输出结构化结果
        ]
        
        is_plus = "plus" in plan_name.lower()
        if payment_method == "gopay":
            cmd.append("--gopay")
            if gopay_otp_file:
                cmd.extend(["--gopay-otp-file", gopay_otp_file])
        elif payment_method == "paypal":
            cmd.append("--paypal")
            if is_plus:
                _log("⚠ Plus 计划使用 PayPal：OpenAI Plus 可能不支持 PayPal，如 confirm 400 请切换 payment_method=card")

        _log(f"CMD: {' '.join(cmd)}")
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        if str(vlm_base_url or "").strip():
            child_env["CTF_VLM_BASE_URL"] = str(vlm_base_url).strip()
        if str(vlm_api_key or "").strip():
            child_env["CTF_VLM_API_KEY"] = str(vlm_api_key).strip()
        if str(vlm_model or "").strip():
            child_env["CTF_VLM_MODEL"] = str(vlm_model).strip()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,  # 防止子进程在 gopay cli_otp_provider 或其他 input() 处永久阻塞
            cwd=os.path.dirname(card_py),
            env=child_env,
            encoding="utf-8",
            errors="replace",
        )

        stdout_lines = []
        result_json_line = None
        _drain_lock = threading.Lock()
        _early_kill_reason = [None]  # mutable container for thread access

        # ── 快速失败模式 ──────────────────────────────────────────────────
        # card.py 内部把 approve blocked 误认为 "需要解 captcha" 导致白等 180s。
        # 我们在读 stdout 时检测这些信号，立刻 kill 子进程。
        _FAST_FAIL_PATTERNS = [
            "manual_approval approve blocked",
            "approve blocked",
        ]

        # 用后台线程实时转发子进程 stdout，主线程负责超时管控。
        # 直接在主线程 for-loop 读取会导致：子进程阻塞（如等待 stdin OTP 或浏览器挂起）
        # 时子进程无输出，主线程的 for-loop 也永远不迭代，timeout 检查代码永远不执行。
        assert proc.stdout is not None

        def _drain_stdout():
            nonlocal result_json_line
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                if line.startswith("CARD_RESULT_JSON="):
                    with _drain_lock:
                        result_json_line = line[len("CARD_RESULT_JSON="):]
                elif line.startswith("CARD_RESULT_JSON:"):
                    with _drain_lock:
                        result_json_line = line[len("CARD_RESULT_JSON:"):]
                else:
                    with _drain_lock:
                        stdout_lines.append(line)
                    _log(line)
                    # 快速失败检测
                    line_lower = line.lower()
                    for pattern in _FAST_FAIL_PATTERNS:
                        if pattern in line_lower:
                            _early_kill_reason[0] = pattern
                            _log(f"⚡ 检测到不可恢复错误 [{pattern}]，立即终止 card.py")
                            _terminate_process_tree(proc)
                            return

        drain_thread = threading.Thread(target=_drain_stdout, daemon=True)
        drain_thread.start()

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(proc)
            proc.wait()
            raise PaymentError(f"支付超时（>{timeout}s）")

        drain_thread.join(timeout=10)

        stderr_text = ""
        if proc.stderr:
            stderr_text = proc.stderr.read()
            if stderr_text.strip():
                _log(f"[stderr] {stderr_text[:600]}")

        if proc.returncode != 0 and not result_json_line:
            err_snippet = stderr_text[:400] or "\n".join(stdout_lines[-10:])
            raise PaymentError(f"card.py 退出码={proc.returncode}: {err_snippet}")

        # 解析结果
        if not result_json_line:
            # 尝试从 stdout 末尾找结果行
            for line in reversed(stdout_lines):
                if "CARD_RESULT_JSON=" in line:
                    result_json_line = line.split("CARD_RESULT_JSON=", 1)[1]
                    break
                if "CARD_RESULT_JSON:" in line:
                    result_json_line = line.split("CARD_RESULT_JSON:", 1)[1]
                    break

        if not result_json_line:
            _log("card.py 未输出 CARD_RESULT_JSON，尝试从日志推断结果")
            diag = _extract_diagnostic_from_stdout(stdout_lines, payment_method)
            full_output = "\n".join(stdout_lines)
            if "succeeded" in full_output.lower():
                return PaymentResult(success=True, state="succeeded", provider=payment_method)
            elif "declined" in full_output.lower():
                return PaymentResult(success=False, state="declined",
                                     error="信用卡被拒绝，请更换卡片或检查账单信息",
                                     provider=payment_method,
                                     **diag)
            elif "captcha" in full_output.lower() and "失败" in full_output:
                return PaymentResult(success=False, state="captcha_failed",
                                     error="hCaptcha 打码失败，请检查打码平台配置",
                                     provider=payment_method,
                                     **diag)
            else:
                return PaymentResult(success=False, state="no_result",
                                     error="card.py 未返回结构化结果",
                                     provider=payment_method,
                                     **diag)

        try:
            result_data = json.loads(result_json_line)
        except Exception as e:
            raise PaymentError(f"card.py 结果 JSON 解析失败: {e}  raw={result_json_line[:200]}")

        raw_state = str(result_data.get("state", "")).strip().lower()
        state = "succeeded" if raw_state == "success" else raw_state
        return_url = result_data.get("return_url", "") or result_data.get("receipt_url", "")
        err_obj = result_data.get("error") or {}
        err_msg = ""
        if isinstance(err_obj, dict):
            err_msg = err_obj.get("message", "") or err_obj.get("decline_code", "") or str(err_obj)
        elif err_obj:
            err_msg = str(err_obj)

        # skipped_not_free: 按 skip_if_not_free=True 跳过（promo 未生效，账号保留 free）
        if state == "skipped_not_free" or bool(result_data.get("skipped")):
            reason = str(result_data.get("reason") or "promo_not_applied")
            _log(f"支付跳过（保留 free 账号）: {reason}")
            # 如果配了 promo 代理但 promo 没生效 → 标记 retryable，让重试策略换 IP
            promo_proxy_set = bool(os.environ.get("_PAYMENT_PROMO_PROXY_ACTIVE"))
            if promo_proxy_set:
                _log("⚠️ Promo 代理已配但首月免费未生效 → 标记为可重试（可能需换 IP/地区）")
                return PaymentResult(
                    success=False,
                    state="skipped_not_free",
                    error=reason,
                    receipt_url="",
                    provider=payment_method,
                    diagnostic_code="skipped_not_free",
                    retryable=True,
                )
            return PaymentResult(
                success=True,
                state="skipped_not_free",
                error="",
                receipt_url="",
                provider=payment_method,
            )

        success = state == "succeeded"
        # 失败时从 stdout 提取精确诊断
        diag = {}
        if not success:
            diag = _extract_diagnostic_from_stdout(stdout_lines, payment_method)
            _log(f"支付诊断: diagnostic_code={diag.get('diagnostic_code')} stage={diag.get('stage')} retryable={diag.get('retryable')}")
        _log(f"支付结果: state={state} success={success} return_url={return_url[:80]}")

        return PaymentResult(
            success=success,
            state=state,
            error=err_msg,
            receipt_url=return_url,
            provider=payment_method,
            **diag,
        )

    finally:
        try:
            os.unlink(cfg_path)
        except Exception:
            pass


def run_payment_from_config_store(
    *,
    plan_name: str,
    access_token: str,
    session_token: str = "",
    cookie_header: str = "",
    device_id: str = "",
    proxy_url: str = "",
    proxy_geo_country: str = "",
    config_overrides: Optional[dict] = None,
    log_fn=None,
) -> PaymentResult:
    """
    从 config_store（全局配置数据库）读取信用卡和打码配置，
    再调用 run_payment()。

    这是给 tasks.py 钩子调用的简化入口。
    """
    _log = log_fn or (lambda msg: logger.info("[payment] %s", msg))
    cfg = _payment_config_with_overrides(config_overrides)
    if not str(cfg.get("payment_auto_plan") or cfg.get("auto_pay_plan") or "").strip():
        normalized_plan_name = str(plan_name or "").strip().lower()
        if "plus" in normalized_plan_name:
            cfg["payment_auto_plan"] = "plus"
        elif "team" in normalized_plan_name:
            cfg["payment_auto_plan"] = "team"
    validate_payment_config(cfg)

    card_number    = (cfg.get("payment_card_number")    or "").strip()
    card_cvc       = (cfg.get("payment_card_cvc")       or "").strip()
    card_exp_month = (cfg.get("payment_card_exp_month") or "").strip()
    card_exp_year  = (cfg.get("payment_card_exp_year")  or "").strip()
    billing_name   = (cfg.get("payment_billing_name")   or "").strip()
    provider, payment_method = resolve_provider(cfg)
    _log(f"支付 provider={provider} method={payment_method}")

    # ── manual_link: 只产出支付链接，不做自动扣款 ──
    if provider == PROVIDER_MANUAL_LINK:
        _log("manual_link provider: 跳过自动支付，仅标记状态")
        return PaymentResult(
            success=True,
            state="manual_link_pending",
            provider=provider,
        )

    # ── gopay_android: 走模拟机实验路线 ──
    if provider == PROVIDER_GOPAY_ANDROID:
        _log("gopay_android provider: 启动 Android 模拟机实验")
        try:
            from platforms.chatgpt.gopay_android_provider import (
                run_gopay_android_experiment,
                AndroidExperimentReport,
            )
            # ⚙️ 若缺 phone/pin 且开启了 auto_register，先跑一次 GoPay 自动注册，
            # 确保后续模拟机登录和 OTP 收码使用同一个 SMSBower activation（phone+pin+activation_id 一套）
            _android_auto_reg_flag = str(cfg.get("payment_gopay_auto_register") or "").strip().lower()
            _android_need_register = (
                _android_auto_reg_flag in {"1", "true", "yes", "on"}
                and not (
                    (cfg.get("payment_gopay_phone") or "").strip()
                    and (cfg.get("payment_gopay_pin") or "").strip()
                )
            )
            if _android_need_register:
                try:
                    from .gopay_auto_register import auto_register_gopay_if_needed
                    cfg_for_reg = dict(cfg)
                    cfg_for_reg["proxy_url"] = proxy_url
                    _log("⚙️ [Android] 前置自动注册 GoPay 账号（SMSBower + Gojek + set_pin）")
                    _acct = auto_register_gopay_if_needed(cfg_for_reg, log_fn=_log)
                    if _acct:
                        cfg["payment_gopay_phone"] = f"+{_acct.country_code} {_acct.phone_number}"
                        cfg["payment_gopay_pin"] = _acct.pin
                        if getattr(_acct, "activation_id", ""):
                            cfg["payment_gopay_sms_activation_id"] = str(_acct.activation_id)
                        if getattr(_acct, "access_token", ""):
                            cfg["payment_gopay_access_token"] = str(_acct.access_token)
                        # 持久化供下次复用（避免再花钱买号）
                        try:
                            from core.config_store import config_store as _cs
                            _persist = {
                                "payment_gopay_phone": cfg["payment_gopay_phone"],
                                "payment_gopay_pin": cfg["payment_gopay_pin"],
                            }
                            if cfg.get("payment_gopay_sms_activation_id"):
                                _persist["payment_gopay_sms_activation_id"] = cfg["payment_gopay_sms_activation_id"]
                            if cfg.get("payment_gopay_access_token"):
                                _persist["payment_gopay_access_token"] = cfg["payment_gopay_access_token"]
                            _cs.set_many(_persist)
                        except Exception:
                            pass
                        _log(f"✅ [Android] 前置注册完成 phone={cfg['payment_gopay_phone']} activation_id={cfg.get('payment_gopay_sms_activation_id','')}")
                except Exception as _reg_exc:
                    _log(f"⚠️ [Android] 前置自动注册失败: {_reg_exc}")
                    return PaymentResult(
                        success=False, state="experiment_error", provider=provider,
                        stage="gopay_android_auto_register",
                        diagnostic_code="android_auto_register_failed",
                        retryable=True,
                        error=str(_reg_exc)[:200],
                    )
            cfg_for_android = dict(cfg)
            cfg_for_android["proxy_url"] = proxy_url
            experiment = run_gopay_android_experiment(cfg_for_android, log_fn=_log)
            # 把实验报告转为 PaymentResult
            if experiment.payment_completed:
                return PaymentResult(
                    success=True, state="succeeded", provider=provider,
                    stage=experiment.stage, diagnostic_code="",
                )
            elif experiment.diagnostic_code == "payment_confirm_timeout":
                # 全流程跑完但未检测到成功确认——可能实际已成功
                return PaymentResult(
                    success=False, state="payment_confirm_timeout", provider=provider,
                    stage=experiment.stage,
                    diagnostic_code="payment_confirm_timeout",
                    retryable=True,
                    error=experiment.error or "支付流程执行完毕但未检测到成功确认",
                )
            elif experiment.auth_page_reached:
                return PaymentResult(
                    success=False, state="experiment_auth_ready", provider=provider,
                    stage=experiment.stage,
                    diagnostic_code=experiment.diagnostic_code or "auth_ready_no_payment",
                    retryable=True,
                    error="GoPay 授权页可达但未完成支付",
                )
            else:
                return PaymentResult(
                    success=False, state="experiment_incomplete", provider=provider,
                    stage=experiment.stage,
                    diagnostic_code=experiment.diagnostic_code,
                    retryable=experiment.retryable,
                    error=experiment.error or f"流程停止在 {experiment.stage}",
                )
        except Exception as exc:
            _log(f"gopay_android 实验异常: {exc}")
            return PaymentResult(
                success=False, state="experiment_error", provider=provider,
                stage="gopay_android_device_init",
                diagnostic_code="android_exception",
                retryable=True,
                error=str(exc)[:200],
            )
    
    paypal_email    = (cfg.get("payment_paypal_email")    or "").strip()
    paypal_password = (cfg.get("payment_paypal_password") or "").strip()
    gopay_phone     = (cfg.get("payment_gopay_phone")     or "").strip()
    gopay_pin       = (cfg.get("payment_gopay_pin")       or "").strip()
    gopay_otp_file  = (cfg.get("payment_gopay_otp_file")  or "").strip()
    gopay_otp_url   = (cfg.get("payment_gopay_otp_url")   or "").strip()

    def _default_gopay_otp_file() -> str:
        return os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "runtime", "wa_relay", "wa-otp.txt")
        )

    # GoPay 全自动 OTP：未显式配置时，回退到 runtime/wa_relay 的默认 OTP 文件
    if payment_method == "gopay" and gopay_phone and gopay_pin and not gopay_otp_file and not gopay_otp_url:
        gopay_otp_file = _default_gopay_otp_file()

    payment_python_executable = (cfg.get("payment_python_executable") or "").strip()

    if payment_method == "paypal":
        _log("PayPal payment selected; strict mode enabled, will not fallback to card")

    if payment_method == "card" and not card_number:
        raise PaymentError("支付方式为 card，但未配置信用卡号 (payment_card_number)，跳过自动支付")

    # ⭐ GoPay 自动注册（无限循环开 Plus 试用的核心环节）
    # 当 payment_method=gopay 且 payment_gopay_auto_register=1 时，
    # 自动从 SMSBOWER 买印尼号 → 调 Gojek API 注册 → 设置 PIN
    if payment_method == "gopay" and str(cfg.get("payment_gopay_auto_register") or "").strip().lower() in {"1", "true", "yes", "on"}:
        if not (gopay_phone and gopay_pin):
            try:
                from .gopay_auto_register import auto_register_gopay_if_needed, GoPayAutoRegisterError
                # 把代理 URL 透传以便 SMSBOWER/Gojek 调用走代理
                cfg_for_reg = dict(cfg)
                cfg_for_reg["proxy_url"] = proxy_url
                _log("⚙️ GoPay 自动注册启动（SMSBOWER 印尼号 + Gojek API + PIN 设置）")
                acct = auto_register_gopay_if_needed(cfg_for_reg, log_fn=_log)
                if acct:
                    gopay_phone = f"+{acct.country_code} {acct.phone_number}"
                    gopay_pin = acct.pin
                    _log(f"✅ 自动注册成功: phone={gopay_phone} pin=***{gopay_pin[-2:]}")
                    # 持久化到 config_store 以便复用（可选，避免重复注册）
                    # 关键：activation_id 必须持久化 —— 模拟机路线收 OTP 时要复用它，
                    # 否则会重新从 SMSBower 买一个新号，登录号和收码号对不上。
                    try:
                        from core.config_store import config_store as _cs
                        persist_payload = {
                            "payment_gopay_phone": gopay_phone,
                            "payment_gopay_pin": gopay_pin,
                        }
                        if getattr(acct, "activation_id", ""):
                            persist_payload["payment_gopay_sms_activation_id"] = str(acct.activation_id)
                        if getattr(acct, "access_token", ""):
                            persist_payload["payment_gopay_access_token"] = str(acct.access_token)
                        _cs.set_many(persist_payload)
                        # 同步把 activation 也写回当前 cfg，保证下游 gopay_android 能拿到
                        cfg["payment_gopay_sms_activation_id"] = persist_payload.get(
                            "payment_gopay_sms_activation_id", ""
                        )
                    except Exception:
                        pass
            except Exception as exc:
                _log(f"⚠️ GoPay 自动注册失败: {exc}（如已有手动配置仍可继续）")
                if not (gopay_phone and gopay_pin):
                    raise PaymentError(f"GoPay 自动注册失败且无手动配置: {exc}")

    if payment_method == "gopay" and gopay_phone and gopay_pin and not gopay_otp_file and not gopay_otp_url:
        gopay_otp_file = _default_gopay_otp_file()

    # ── 代理池智能选择 + Promo 代理策略 ─────────────────────────────────
    # 代理池格式：逗号分隔，支持地区标签
    #   payment_proxy_pool = jp:http://jp-proxy:8080, eu:http://eu-proxy:8080, http://generic:8080
    #   标签格式: geo:url （不区分大小写）
    # 自动策略：
    #   1. PayPal 支付 → 优先用带 promo 标签的 JP 代理（触发首月免费）
    #   2. 如果没有 JP 代理 → 用 EU 代理（PayPal 兼容）
    #   3. 都没有 → 用默认 proxy_url
    pool_raw = (cfg.get("payment_proxy_pool") or "").strip()
    _geo_pool: dict[str, str] = {}  # geo → url
    _plain_pool: list[str] = []
    if pool_raw:
        for entry in pool_raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry and not entry.startswith("http"):
                # geo:url 格式
                parts = entry.split(":", 1)
                geo_tag = parts[0].strip().upper()
                url_part = parts[1].strip()
                # 处理 geo:http:// 的情况（冒号后面直接跟 http）
                if not url_part.startswith("http"):
                    # 可能是 jp:http://... 被 split 了第一个冒号
                    # 重新解析
                    idx = entry.index(":")
                    geo_tag = entry[:idx].strip().upper()
                    url_part = entry[idx + 1:].strip()
                _geo_pool[geo_tag] = url_part
                _log(f"  代理池: [{geo_tag}] → {url_part[:40]}...")
            else:
                _plain_pool.append(entry)

    # 显式配置的 promo 代理优先
    promo_proxy = (cfg.get("payment_promo_proxy_url") or "").strip()
    promo_proxy_geo = (cfg.get("payment_promo_proxy_geo") or "JP").strip().upper()

    # 自动从池中选择 promo 代理（JP → 触发首月免费）
    if not promo_proxy:
        for geo_candidate in ("JP",):
            if geo_candidate in _geo_pool:
                promo_proxy = _geo_pool[geo_candidate]
                promo_proxy_geo = geo_candidate
                _log(f"🎯 自动从代理池选择 Promo 代理: [{geo_candidate}] {promo_proxy[:40]}...")
                break

    if promo_proxy and payment_method == "paypal":
        _log(f"🎯 Promo 策略: {promo_proxy_geo} IP 创建 checkout → 首月免费 → PayPal 同 IP 支付")
        proxy_url = promo_proxy
        proxy_geo_country = promo_proxy_geo
        os.environ["_PAYMENT_PROMO_PROXY_ACTIVE"] = "1"
    elif promo_proxy and payment_method == "card":
        _log(f"🎯 Promo 策略: {promo_proxy_geo} IP 创建 checkout → 首月免费 → card 支付")
        proxy_url = promo_proxy
        proxy_geo_country = promo_proxy_geo
        os.environ["_PAYMENT_PROMO_PROXY_ACTIVE"] = "1"
    else:
        os.environ.pop("_PAYMENT_PROMO_PROXY_ACTIVE", None)

    billing_country = (cfg.get("payment_billing_country") or proxy_geo_country or "US").strip().upper()
    billing_currency = (cfg.get("payment_billing_currency") or _COUNTRY_TO_CURRENCY.get(billing_country, "USD")).strip().upper()
    billing_address = (cfg.get("payment_billing_address") or "").strip()
    billing_city = (cfg.get("payment_billing_city") or "").strip()
    billing_state = (cfg.get("payment_billing_state") or "").strip()
    billing_zip = (cfg.get("payment_billing_zip") or "").strip()

    if payment_method == "paypal":
        # ── PayPal billing 策略 ──
        # 优先级:
        #   1. 如果有 promo 代理且 geo 在 PayPal 兼容国 → 用 promo 国家 billing（如 JP）
        #   2. 如果 billing_country 在 PayPal 兼容国 → 保持
        #   3. fallback → IE billing
        if promo_proxy and promo_proxy_geo in _PAYPAL_COMPATIBLE_COUNTRIES:
            # promo 国家可以直接用于 PayPal billing
            # 但如果该国家的 Stripe checkout 不支持 PayPal（如 JP），
            # 则改用 IE/EUR billing（promo 仍然生效，实测 due=0 EUR）
            effective_geo = promo_proxy_geo
            if promo_proxy_geo in _PAYPAL_STRIPE_BLOCKED_BILLING:
                effective_geo = "IE"
                _log(
                    f"PayPal + Promo: {promo_proxy_geo} Stripe checkout 不支持 PayPal，"
                    f"billing 改用 IE/EUR（promo 仍生效）"
                )
            defaults = _PROMO_BILLING_DEFAULTS.get(effective_geo, _PAYPAL_DEFAULT_BILLING)
            billing_country = defaults["country"]
            billing_currency = defaults["currency"]
            billing_address = defaults["address"]
            billing_city = defaults["city"]
            billing_state = defaults["state"]
            billing_zip = defaults["zip"]
            _log(f"PayPal + Promo: billing → {billing_country}/{billing_currency}（代理 IP={promo_proxy_geo}）")
        elif billing_country not in _PAYPAL_COMPATIBLE_COUNTRIES:
            _log(
                f"PayPal billing {billing_country}/{billing_currency} 不在兼容列表 → "
                f"fallback {_PAYPAL_DEFAULT_BILLING['country']}/{_PAYPAL_DEFAULT_BILLING['currency']}"
            )
            billing_country = _PAYPAL_DEFAULT_BILLING["country"]
            billing_currency = _PAYPAL_DEFAULT_BILLING["currency"]
            billing_address = billing_address or _PAYPAL_DEFAULT_BILLING["address"]
            billing_city = billing_city or _PAYPAL_DEFAULT_BILLING["city"]
            billing_state = billing_state or _PAYPAL_DEFAULT_BILLING["state"]
            billing_zip = billing_zip or _PAYPAL_DEFAULT_BILLING["zip"]

        # IE Eircode 特殊修正
        if billing_country == "IE" and billing_zip.replace(" ", "").upper() != _PAYPAL_DEFAULT_BILLING["zip"].replace(" ", "").upper():
            _log(f"PayPal IE billing Eircode {billing_zip or '<empty>'} may be rejected; using {_PAYPAL_DEFAULT_BILLING['zip']}")
            billing_address = _PAYPAL_DEFAULT_BILLING["address"]
            billing_city = _PAYPAL_DEFAULT_BILLING["city"]
            billing_state = _PAYPAL_DEFAULT_BILLING["state"]
            billing_zip = _PAYPAL_DEFAULT_BILLING["zip"]

        proxy_geo = str(proxy_geo_country or "").strip().upper()
        if proxy_geo and proxy_geo not in _PAYPAL_COMPATIBLE_COUNTRIES:
            _log(f"⚠️ PayPal 注意: 代理 IP 地区={proxy_geo} 不在 PayPal 兼容列表，可能触发风控")
        elif not proxy_geo:
            _log("⚠️ PayPal 警告: 未检测到代理地理位置(proxy_geo_country)")

        # PayPal 专用代理覆盖（仅在没有 promo 代理时生效，避免冲突）
        if not promo_proxy:
            paypal_proxy = (cfg.get("payment_paypal_proxy_url") or "").strip()
            if paypal_proxy:
                _log(f"PayPal 使用专用代理: {paypal_proxy[:40]}...")
                proxy_url = paypal_proxy

    vlm_cfg = _resolve_vlm_config(cfg)
    if vlm_cfg.get("base_url") and vlm_cfg.get("api_key"):
        _log(f"VLM solver enabled: base_url={vlm_cfg.get('base_url')} model={vlm_cfg.get('model')}")

    return run_payment(
        plan_name=plan_name,
        access_token=access_token,
        payment_method=payment_method,
        session_token=session_token,
        cookie_header=cookie_header,
        device_id=device_id,
        card_number=card_number,
        card_cvc=card_cvc,
        card_exp_month=card_exp_month,
        card_exp_year=card_exp_year,
        billing_name=billing_name,
        billing_country=billing_country,
        billing_address=billing_address,
        billing_city=billing_city,
        billing_state=billing_state,
        billing_zip=billing_zip,
        billing_currency=billing_currency,
        team_workspace_name=(cfg.get("payment_team_workspace_name") or "MyWorkspace").strip(),
        team_seat_quantity=int(cfg.get("payment_team_seat_quantity") or 5),
        captcha_api_url=(cfg.get("payment_captcha_api_url") or "").strip(),
        captcha_key=(cfg.get("payment_captcha_key")      or "").strip(),
        paypal_email=paypal_email,
        paypal_password=paypal_password,
        gopay_phone=gopay_phone,
        gopay_pin=gopay_pin,
        gopay_otp_file=gopay_otp_file,
        gopay_otp_url=gopay_otp_url,
        proxy_url=proxy_url,
        proxy_geo_country=proxy_geo_country,
        python_executable=payment_python_executable,
        skip_if_not_free=str(cfg.get("payment_skip_if_not_free") or "1").strip().lower() not in {"0", "false", "no", ""},
        auto_cancel_after_subscribe=str(cfg.get("payment_auto_cancel_after_subscribe") or "1").strip().lower() not in {"0", "false", "no", ""},
        is_coupon_from_query_param=str(cfg.get("payment_is_coupon_from_query_param") or "0").strip().lower() in {"1", "true", "yes", "on"},
        checkout_ui_mode=(cfg.get("payment_checkout_ui_mode") or "custom").strip(),
        vlm_base_url=vlm_cfg.get("base_url", ""),
        vlm_api_key=vlm_cfg.get("api_key", ""),
        vlm_model=vlm_cfg.get("model", ""),
        vlm_timeout_s=vlm_cfg.get("timeout_s", "45"),
        log_fn=log_fn,
    )
