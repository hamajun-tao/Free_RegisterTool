"""GoPay 全自动注册模块（无限循环开 Plus 试用的核心组件）

完整流程（参考 xwuxl.com 教程）：
1. 从 SMSBOWER 购买印尼手机号（country=6, service=ot 任意服务）
2. 调用 Gojek API 注册账号（goid.gojekapi.com）
   - POST /goid/login/request → 触发 OTP 短信
   - 从 SMSBOWER 接收 OTP
   - POST /goid/otp/validate → 验证
   - POST /goid/token → 拿 access_token
3. 设置 GoPay PIN（customer.gopayapi.com）
4. 返回 (phone, pin, country_code) 供 ChatGPT Plus 支付使用
5. 支付时扣 1 印尼盾 → ChatGPT 试用激活 → 1 盾自动退回
6. 支付成功后调用 cancel API → 试用期结束不续费

每轮约 10 分钟，理论上无限循环开新 ChatGPT 账号 + Plus 试用。

⚠️ 注意：
- Gojek 内部 API 可能随时变化（headers、签名、风控）
- 印尼接码号经常被 Gojek 列入黑名单，建议轮换号源
- 此模块作为"自动注册"选项；若失败回退手动配置的 phone+pin
"""
from __future__ import annotations

import os
import re
import time
import uuid
import hashlib
import logging
import secrets
import platform
from dataclasses import dataclass
from typing import Optional, Callable

logger = logging.getLogger(__name__)


def _config_flag_enabled(value, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on", "enabled"}

# Gojek 客户端版本（需定期更新；可通过环境变量 / 配置覆盖）
DEFAULT_GOJEK_APP_VERSION = "4.93.2"
DEFAULT_GOJEK_USER_AGENT = (
    "Gojek/4.93.2 (samsung SM-S928B; Android 14; en) "
    "okhttp/4.12.0"
)

# ── 号码质量跟踪（进程内统计，重启后重置） ──────────────────────────
_NUMBER_STATS: dict[str, dict[str, int]] = {}
# 越南 > 印尼 > 菲律宾 > 马来西亚（经验值，印尼号常被拉黑）
_FALLBACK_COUNTRIES = ["10", "6", "63", "60"]
_MAX_CONSECUTIVE_FAILURES_BEFORE_SWITCH = 3

# Gojek API endpoints
GOID_BASE = "https://goid.gojekapi.com"
GOPAY_CUSTOMER_BASE = "https://customer.gopayapi.com"


@dataclass
class GoPayAccount:
    """完成注册后的 GoPay 账号"""
    phone_number: str        # 不含国家码的本地号码（如 8123456789）
    country_code: str         # 国家码（"62" 印尼）
    pin: str                  # 6 位 PIN
    access_token: str = ""    # Gojek access token（后续可复用）
    refresh_token: str = ""
    user_id: str = ""
    activation_id: str = ""   # SMSBower activation_id（模拟机路线收 OTP 时必须复用）

    def to_dict(self) -> dict:
        return {
            "phone_number": self.phone_number,
            "country_code": self.country_code,
            "pin": self.pin,
            "access_token": self.access_token[:20] + "..." if self.access_token else "",
            "user_id": self.user_id,
            "activation_id": self.activation_id,
        }


class GoPayAutoRegisterError(RuntimeError):
    """GoPay 自动注册失败"""
    pass


def _generate_device_id() -> str:
    """生成稳定的设备 ID（每个 session 一个）"""
    raw = f"{uuid.uuid4()}-{platform.system()}-{secrets.token_hex(8)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _generate_unique_id() -> str:
    """Gojek X-UniqueId header 用的伪 IMEI"""
    # 模拟 Android 15 位 IMEI
    return "".join(secrets.choice("0123456789") for _ in range(15))


def _build_gojek_headers(
    *,
    device_id: str,
    unique_id: str,
    app_version: str = DEFAULT_GOJEK_APP_VERSION,
    user_agent: str = DEFAULT_GOJEK_USER_AGENT,
    extra: Optional[dict] = None,
) -> dict:
    """构建 Gojek API 请求 headers"""
    headers = {
        "Accept": "application/json",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "X-AppId": "com.gojek.app",
        "X-AppVersion": app_version,
        "X-DeviceOS": "Android,13",
        "X-Platform": "Android",
        "X-DeviceId": device_id,
        "X-UniqueId": unique_id,
        "X-Session-Id": str(uuid.uuid4()),
        "X-RequestId": str(uuid.uuid4()),
        "X-Location": "-6.21462,106.84513",  # 雅加达坐标
        "X-Appversion": app_version,  # 兼容旧字段
    }
    if extra:
        headers.update(extra)
    return headers


class GoPayRegistrar:
    """GoPay 自动注册器 - 集成 SMSBOWER 接码 + Gojek API"""

    def __init__(
        self,
        *,
        smsbower_api_key: str,
        proxy_url: str = "",
        log_fn: Optional[Callable[[str], None]] = None,
        otp_timeout_seconds: int = 180,
        sms_country: str = "6",  # SMSBOWER country=6 印尼
        sms_service: str = "ot",  # 通用 service（OTP）
        gojek_app_version: str = "",
        auto_switch_country: bool = True,
        otp_max_retries: int = 2,
    ):
        self.api_key = smsbower_api_key
        self.proxy_url = proxy_url
        self._log = log_fn or (lambda msg: logger.info("[gopay-reg] %s", msg))
        self.otp_timeout = otp_timeout_seconds
        self.sms_country = sms_country
        self.sms_service = sms_service
        self.auto_switch_country = auto_switch_country
        self.otp_max_retries = max(1, otp_max_retries)
        # 允许运行时覆盖 Gojek 版本号
        self._app_version = (gojek_app_version or "").strip() or DEFAULT_GOJEK_APP_VERSION
        self._user_agent = DEFAULT_GOJEK_USER_AGENT.replace(DEFAULT_GOJEK_APP_VERSION, self._app_version)
        self.device_id = _generate_device_id()
        self.unique_id = _generate_unique_id()
        self._activation_id: Optional[str] = None
        self._http = self._build_http()

    def _build_http(self):
        """优先用 curl_cffi 模拟真实 Android 客户端 TLS 指纹"""
        try:
            from curl_cffi.requests import Session as _CurlSession
            sess = _CurlSession(impersonate="chrome136")
        except ImportError:
            import requests
            sess = requests.Session()
        if self.proxy_url:
            sess.proxies = {"http": self.proxy_url, "https": self.proxy_url}
        return sess

    # ───── 步骤 1: 从 SMSBOWER 获取印尼号 ─────

    def acquire_phone(self) -> str:
        """从 SMSBOWER 购买一个印尼号，返回纯数字（含 62 国家码）"""
        from core.smsbower import SmsBowerClient, SmsBowerError, SmsBowerNoNumberError

        client = SmsBowerClient(self.api_key)
        try:
            self._log(f"从 SMSBOWER 申请印尼手机号 (country={self.sms_country}, service={self.sms_service})...")
            num = client.get_number(
                service=self.sms_service,
                country=str(self.sms_country),
            )
            self._activation_id = num.activation_id
            phone_full = re.sub(r"\D", "", str(num.phone_number))
            self._log(f"获得手机号: +{phone_full} (activation_id={num.activation_id})")
            return phone_full
        except SmsBowerNoNumberError as exc:
            raise GoPayAutoRegisterError(f"SMSBOWER 没有可用印尼号: {exc}")
        except SmsBowerError as exc:
            raise GoPayAutoRegisterError(f"SMSBOWER 错误: {exc}")

    def receive_otp(self) -> str:
        """从 SMSBOWER 等待并接收 OTP（使用 wait_for_code 方法，自动轮询）"""
        if not self._activation_id:
            raise GoPayAutoRegisterError("尚未购买号码，无 activation_id")
        from core.smsbower import SmsBowerClient, SmsBowerTimeoutError

        client = SmsBowerClient(self.api_key)
        try:
            client.set_status(self._activation_id, status=1)
        except Exception as exc:
            self._log(f"set_status(1) 异常（可忽略）: {exc}")

        self._log(f"等待 SMSBOWER 接收 OTP (timeout={self.otp_timeout}s)...")
        try:
            code = client.wait_for_code(
                self._activation_id,
                timeout=self.otp_timeout,
                interval=5,
            )
            self._log(f"收到 OTP: {code}")
            return str(code)
        except SmsBowerTimeoutError as exc:
            raise GoPayAutoRegisterError(f"OTP 等待超时: {exc}")
        except Exception as exc:
            raise GoPayAutoRegisterError(f"接收 OTP 失败: {exc}")

    def release_phone(self, success: bool = True):
        """释放号码（成功标记 6=完成 / 失败 8=取消）"""
        if not self._activation_id:
            return
        from core.smsbower import SmsBowerClient
        try:
            client = SmsBowerClient(self.api_key)
            client.set_status(self._activation_id, status=6 if success else 8)
        except Exception:
            pass

    # ───── 步骤 2: Gojek 注册（请求 OTP + 验证 + 获取 token）─────

    def _record_stat(self, country: str, outcome: str):
        """记录号码质量统计"""
        if country not in _NUMBER_STATS:
            _NUMBER_STATS[country] = {"success": 0, "fail": 0, "otp_timeout": 0}
        _NUMBER_STATS[country][outcome] = _NUMBER_STATS[country].get(outcome, 0) + 1

    def _should_switch_country(self) -> Optional[str]:
        """检查当前国家是否连续失败过多，返回建议切换到的国家码"""
        if not self.auto_switch_country:
            return None
        stats = _NUMBER_STATS.get(self.sms_country, {})
        recent_fails = stats.get("fail", 0) + stats.get("otp_timeout", 0)
        recent_ok = stats.get("success", 0)
        if recent_fails >= _MAX_CONSECUTIVE_FAILURES_BEFORE_SWITCH and recent_ok == 0:
            for alt in _FALLBACK_COUNTRIES:
                if alt != self.sms_country:
                    alt_stats = _NUMBER_STATS.get(alt, {})
                    if alt_stats.get("fail", 0) + alt_stats.get("otp_timeout", 0) < _MAX_CONSECUTIVE_FAILURES_BEFORE_SWITCH:
                        return alt
        return None

    def request_login_otp(self, phone_full: str) -> dict:
        """POST /goid/login/request - Gojek 触发发 SMS OTP
        
        body: {"country_code": "+62", "phone": "8XXXX"}
        """
        # 拆分国家码和本地号
        if phone_full.startswith("62"):
            country_code = "+62"
            local_phone = phone_full[2:]
        else:
            country_code = "+62"
            local_phone = phone_full

        url = f"{GOID_BASE}/goid/login/request"
        body = {"country_code": country_code, "phone": local_phone}
        headers = _build_gojek_headers(
            device_id=self.device_id, unique_id=self.unique_id,
            app_version=self._app_version, user_agent=self._user_agent,
        )
        self._log(f"POST {url}  body={body}")
        r = self._http.post(url, json=body, headers=headers, timeout=20)
        if r.status_code != 200:
            raise GoPayAutoRegisterError(
                f"login/request 失败 [{r.status_code}]: {r.text[:200]}"
            )
        try:
            data = r.json()
        except Exception:
            data = {}
        self._log(f"login/request OK: {str(data)[:200]}")
        return data

    def validate_otp(self, phone_full: str, otp: str, otp_token: str = "") -> dict:
        """POST /goid/otp/validate - 提交 OTP 验证"""
        if phone_full.startswith("62"):
            country_code = "+62"
            local_phone = phone_full[2:]
        else:
            country_code = "+62"
            local_phone = phone_full

        url = f"{GOID_BASE}/goid/otp/validate"
        body = {
            "country_code": country_code,
            "phone": local_phone,
            "otp": otp,
        }
        if otp_token:
            body["otp_token"] = otp_token
        headers = _build_gojek_headers(
            device_id=self.device_id, unique_id=self.unique_id,
            app_version=self._app_version, user_agent=self._user_agent,
        )
        self._log(f"POST {url}  otp={otp}")
        r = self._http.post(url, json=body, headers=headers, timeout=20)
        if r.status_code not in (200, 201):
            raise GoPayAutoRegisterError(
                f"otp/validate 失败 [{r.status_code}]: {r.text[:200]}"
            )
        return r.json()

    def get_token(self, phone_full: str, otp_token: str) -> dict:
        """POST /goid/token - 用 OTP 验证后的 token 拿 access_token"""
        if phone_full.startswith("62"):
            country_code = "+62"
            local_phone = phone_full[2:]
        else:
            country_code = "+62"
            local_phone = phone_full

        url = f"{GOID_BASE}/goid/token"
        body = {
            "grant_type": "otp",
            "country_code": country_code,
            "phone": local_phone,
            "otp_token": otp_token,
        }
        headers = _build_gojek_headers(
            device_id=self.device_id, unique_id=self.unique_id,
            app_version=self._app_version, user_agent=self._user_agent,
        )
        self._log(f"POST {url}  (token exchange)")
        r = self._http.post(url, json=body, headers=headers, timeout=20)
        if r.status_code not in (200, 201):
            raise GoPayAutoRegisterError(
                f"token 失败 [{r.status_code}]: {r.text[:200]}"
            )
        return r.json()

    # ───── 用 phone + pin 登录已存在 GoPay 账号（用于解绑/复用）─────

    def login_existing_account(
        self,
        phone_full: str,
        pin: str,
    ) -> str:
        """对已有 GoPay 账号用 phone + pin 登录，返回 access_token
        
        流程：
          1. POST /goid/login/request → 触发 OTP
          2. 等 SMSBOWER 收 OTP（如果手机号是接码）/ 或外部传入 OTP
          3. POST /goid/otp/validate → 拿 otp_token
          4. POST /goid/token + grant_type=otp → access_token
          
        注意：这要求手机号能收 OTP；若已用接码买号则无问题。
        """
        # 这里简化：直接复用 register 流程的前 5 步（不调 set_pin）
        # 实际产品里需要支持已有用户的 OTP 通道
        self._log(f"以已有账号登录 GoPay: +{phone_full}")
        try:
            req_resp = self.request_login_otp(phone_full)
            otp_token = str(req_resp.get("otp_token") or "")
            otp = self.receive_otp()
            val_resp = self.validate_otp(phone_full, otp, otp_token)
            session_token = (
                val_resp.get("otp_token")
                or val_resp.get("session_token")
                or otp_token
            )
            tok_resp = self.get_token(phone_full, str(session_token))
            access_token = tok_resp.get("access_token") or ""
            if not access_token:
                raise GoPayAutoRegisterError(f"login: 未拿到 access_token: {tok_resp}")
            self.release_phone(success=True)
            return access_token
        except Exception:
            self.release_phone(success=False)
            raise

    # ───── 解绑功能（让 GoPay 账号可重复 link 到新 ChatGPT 帐号）─────

    def list_linked_apps(self, access_token: str) -> list[dict]:
        """GET 已链接 app 列表"""
        url = f"{GOPAY_CUSTOMER_BASE}/api/v1/users/linked-apps"
        headers = _build_gojek_headers(
            device_id=self.device_id,
            unique_id=self.unique_id,
            extra={"Authorization": f"Bearer {access_token}"},
        )
        try:
            r = self._http.get(url, headers=headers, timeout=20)
            if r.status_code != 200:
                self._log(f"list_linked_apps {r.status_code}: {r.text[:200]}")
                return []
            data = r.json() if r.content else {}
            apps = data.get("apps") or data.get("linked_apps") or data.get("data") or []
            if isinstance(apps, dict):
                apps = list(apps.values())
            return apps if isinstance(apps, list) else []
        except Exception as exc:
            self._log(f"list_linked_apps 异常: {exc}")
            return []

    def unlink_app(self, access_token: str, app_id: str) -> bool:
        """解绑指定 app（merchant_id）。多个候选 endpoint 容错"""
        candidates = [
            ("DELETE", f"{GOPAY_CUSTOMER_BASE}/api/v1/users/linked-apps/{app_id}"),
            ("POST", f"{GOPAY_CUSTOMER_BASE}/api/v1/users/linked-apps/{app_id}/unlink"),
            ("DELETE", f"{GOPAY_CUSTOMER_BASE}/api/v1/users/linkages/{app_id}"),
        ]
        headers = _build_gojek_headers(
            device_id=self.device_id,
            unique_id=self.unique_id,
            extra={"Authorization": f"Bearer {access_token}"},
        )
        for method, url in candidates:
            try:
                if method == "DELETE":
                    r = self._http.delete(url, headers=headers, timeout=20)
                else:
                    r = self._http.post(url, headers=headers, json={}, timeout=20)
                if 200 <= r.status_code < 300:
                    self._log(f"✅ unlink {app_id} via {method} {url} OK")
                    return True
                if r.status_code in (401, 403):
                    self._log(f"unlink {r.status_code} 未授权，停止尝试")
                    break
            except Exception as exc:
                self._log(f"unlink {url} 异常: {exc}")
                continue
        return False

    def unlink_all_apps(
        self,
        access_token: str,
        keep_app_names: tuple[str, ...] = (),
    ) -> int:
        """解绑全部已链接 app（保留 keep_app_names 中的）。
        Returns 解绑成功数量。
        """
        apps = self.list_linked_apps(access_token)
        if not apps:
            self._log("没有已链接 app（或获取失败）")
            return 0
        self._log(f"发现 {len(apps)} 个已链接 app")
        unlinked = 0
        for app in apps:
            app_id = str(app.get("id") or app.get("app_id") or app.get("merchant_id") or "")
            app_name = str(app.get("name") or app.get("app_name") or "")
            if app_name in keep_app_names:
                self._log(f"  跳过 {app_name} (在 keep 列表)")
                continue
            if not app_id:
                continue
            self._log(f"  解绑 {app_name or app_id}...")
            if self.unlink_app(access_token, app_id):
                unlinked += 1
        return unlinked

    # ───── 步骤 3: 设置 GoPay PIN ─────

    def set_pin(self, access_token: str, pin: str) -> bool:
        """POST customer.gopayapi.com/api/v1/users/pin/create - 设置 GoPay PIN"""
        if not (pin.isdigit() and len(pin) == 6):
            raise GoPayAutoRegisterError(f"PIN 必须是 6 位数字: {pin}")
        url = f"{GOPAY_CUSTOMER_BASE}/api/v1/users/pin/create"
        body = {"pin": pin, "confirm_pin": pin}
        headers = _build_gojek_headers(
            device_id=self.device_id,
            unique_id=self.unique_id,
            app_version=self._app_version,
            user_agent=self._user_agent,
            extra={"Authorization": f"Bearer {access_token}"},
        )
        self._log(f"POST {url}  (set PIN)")
        r = self._http.post(url, json=body, headers=headers, timeout=20)
        if r.status_code not in (200, 201, 204):
            raise GoPayAutoRegisterError(
                f"set_pin 失败 [{r.status_code}]: {r.text[:200]}"
            )
        return True

    # ───── 完整流程编排 ─────

    def register(
        self,
        pin: Optional[str] = None,
        *,
        keep_activation_alive: bool = False,
    ) -> GoPayAccount:
        """执行完整的 GoPay 注册流程

        Args:
            pin: 6 位 PIN，留空则自动生成
            keep_activation_alive: 成功时保留 SMSBower activation 不主动 release，
                供 Android 模拟机路线继续用同一个号收下一条 OTP；
                API 路线可传 False（默认），但即便保留 SMSBower 超时也会自动回收，影响可忽略。
        Returns:
            GoPayAccount 含可立即用于 ChatGPT Plus 支付的 phone+pin+activation_id
        """
        if not pin:
            pin = "".join(secrets.choice("0123456789") for _ in range(6))

        # 检查是否需要切换国家
        alt_country = self._should_switch_country()
        if alt_country:
            self._log(f"⚠️ 国家 {self.sms_country} 连续失败过多，自动切换到 {alt_country}")
            self.sms_country = alt_country

        try:
            # 1. 获取手机号
            phone_full = self.acquire_phone()

            # 2. 触发 Gojek 发 OTP
            req_resp = self.request_login_otp(phone_full)
            otp_token = str(req_resp.get("otp_token") or "")

            # 3. 接收 OTP（含重试）
            otp = None
            last_otp_err = None
            for otp_attempt in range(1, self.otp_max_retries + 1):
                try:
                    otp = self.receive_otp()
                    break
                except GoPayAutoRegisterError as otp_exc:
                    last_otp_err = otp_exc
                    self._log(f"OTP 接收失败 (attempt {otp_attempt}/{self.otp_max_retries}): {otp_exc}")
                    if otp_attempt < self.otp_max_retries:
                        self._log("重新触发 OTP ...")
                        try:
                            req_resp2 = self.request_login_otp(phone_full)
                            otp_token = str(req_resp2.get("otp_token") or otp_token)
                        except Exception as re_exc:
                            self._log(f"重新触发 OTP 失败: {re_exc}")
            if otp is None:
                self._record_stat(self.sms_country, "otp_timeout")
                raise GoPayAutoRegisterError(f"OTP 接收最终失败 ({self.otp_max_retries} 次尝试): {last_otp_err}")

            # 4. 验证 OTP
            val_resp = self.validate_otp(phone_full, otp, otp_token)
            session_token = (
                val_resp.get("otp_token")
                or val_resp.get("session_token")
                or otp_token
            )

            # 5. 拿 access_token
            tok_resp = self.get_token(phone_full, str(session_token))
            access_token = tok_resp.get("access_token") or ""
            refresh_token = tok_resp.get("refresh_token") or ""
            if not access_token:
                self._record_stat(self.sms_country, "fail")
                raise GoPayAutoRegisterError(f"未拿到 access_token: {tok_resp}")

            # 6. 设置 PIN
            self.set_pin(access_token, pin)

            if not keep_activation_alive:
                self.release_phone(success=True)
            else:
                self._log("保留 SMSBower activation，供下游模拟机路线复用同一个号收 OTP")
            self._record_stat(self.sms_country, "success")

            country_code = "62"
            local = phone_full[2:] if phone_full.startswith("62") else phone_full
            self._log(
                f"✅ GoPay 注册完成: +{country_code}{local} pin=*** access_token={access_token[:20]}... "
                f"activation_id={self._activation_id or '<none>'}"
            )
            self._log(f"号码统计: {_NUMBER_STATS}")
            return GoPayAccount(
                phone_number=local,
                country_code=country_code,
                pin=pin,
                access_token=access_token,
                refresh_token=refresh_token,
                user_id=str(tok_resp.get("user_id") or ""),
                activation_id=self._activation_id or "",
            )
        except Exception:
            self._record_stat(self.sms_country, "fail")
            self.release_phone(success=False)
            raise


def auto_register_gopay_if_needed(
    cfg: dict,
    *,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Optional[GoPayAccount]:
    """供 payment_auto.py 调用的入口

    当 cfg.payment_method=gopay 但缺少 phone/pin 时，自动注册一个新 GoPay 账号
    返回 None 表示无需自动注册（已有配置）；返回 GoPayAccount 表示新注册的账号
    """
    if str(cfg.get("payment_method") or "").lower() != "gopay":
        return None
    if not _config_flag_enabled(cfg.get("payment_gopay_auto_register"), default=False):
        return None
    # 已有配置就跳过
    has_phone = bool((cfg.get("payment_gopay_phone") or "").strip())
    has_pin = bool((cfg.get("payment_gopay_pin") or "").strip())
    if has_phone and has_pin:
        return None

    smsbower_key = (cfg.get("smsbower_api_key") or "").strip()
    if not smsbower_key:
        raise GoPayAutoRegisterError(
            "payment_gopay_auto_register=true 但未配置 smsbower_api_key"
        )

    proxy_url = (cfg.get("proxy_url") or "").strip()
    pin = (cfg.get("payment_gopay_pin") or "").strip() or None
    sms_country = str(cfg.get("payment_gopay_sms_country") or "6").strip() or "6"
    sms_service = str(cfg.get("payment_gopay_sms_service") or "ot").strip() or "ot"

    try:
        otp_timeout_seconds = int(cfg.get("smsbower_otp_timeout_seconds") or cfg.get("smstome_otp_timeout_seconds") or 180)
    except Exception:
        otp_timeout_seconds = 180

    gojek_ver = str(cfg.get("payment_gojek_app_version") or "").strip()
    otp_retries = 2
    try:
        otp_retries = int(cfg.get("payment_gopay_otp_retries") or 2)
    except Exception:
        pass

    registrar = GoPayRegistrar(
        smsbower_api_key=smsbower_key,
        proxy_url=proxy_url,
        log_fn=log_fn,
        otp_timeout_seconds=otp_timeout_seconds,
        sms_country=sms_country,
        sms_service=sms_service,
        gojek_app_version=gojek_ver,
        otp_max_retries=otp_retries,
    )
    # Android 模拟机路线需要复用 activation_id 继续收下一条 OTP；
    # 判断依据：显式 provider=gopay_android，或配置了模拟器必要字段。
    raw_provider = str(cfg.get("payment_provider") or "").strip().lower()
    _android_signal_keys = (
        "payment_android_avd_name",
        "payment_android_emulator_path",
        "payment_android_serial",
        "payment_android_adb_path",
        "payment_android_gopay_apk_path",
        "payment_android_gojek_apk_path",
    )
    keep_alive = (
        raw_provider == "gopay_android"
        or any(str(cfg.get(k) or "").strip() for k in _android_signal_keys)
    )
    return registrar.register(pin=pin, keep_activation_alive=keep_alive)
