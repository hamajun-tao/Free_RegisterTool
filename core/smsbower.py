"""SMSBOWER 手机接码服务客户端。

Base URL: https://smsbower.page/stubs/handler_api.php
Auth: api_key query parameter
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional, Callable

import requests


BASE_URL = "https://smsbower.page/stubs/handler_api.php"
DEFAULT_TIMEOUT = 300
DEFAULT_POLL_INTERVAL = 5.0


@dataclass
class SmsBowerNumber:
    activation_id: str
    phone_number: str
    service: str
    country: str
    quality: str = ""  # gold / silver / "" = any


class SmsBowerError(RuntimeError):
    pass


class SmsBowerRequestError(SmsBowerError):
    pass


class SmsBowerBalanceError(SmsBowerError):
    pass


class SmsBowerNoNumberError(SmsBowerError):
    pass


class SmsBowerInvalidPhoneExceptionError(SmsBowerError):
    pass


class SmsBowerTimeoutError(SmsBowerError):
    pass


class SmsBowerWaitRetryError(SmsBowerError):
    pass


def _call(api_key: str, params: dict, timeout: float = 30) -> str:
    params["api_key"] = api_key
    try:
        r = requests.get(BASE_URL, params=params, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SmsBowerRequestError(f"SMSBOWER 请求失败: {e}") from e

    text = r.text.strip()
    if text.startswith("BAD_KEY"):
        raise SmsBowerError("SMSBOWER API Key 无效")
    if text.startswith("BAD_ACTION"):
        raise SmsBowerError(f"SMSBOWER 未知操作: {params.get('action', '?')}")
    if text.startswith("NO_BALANCE"):
        raise SmsBowerBalanceError("SMSBOWER 余额不足")
    if text.startswith("NO_NUMBERS"):
        raise SmsBowerNoNumberError("SMSBOWER 无可用号码")
    if text.startswith("ERROR_"):
        raise SmsBowerError(f"SMSBOWER 错误: {text}")
    return text


class SmsBowerClient:
    """SMSBOWER 客户端。"""

    def __init__(self, api_key: str):
        if not api_key:
            raise SmsBowerError("SMSBOWER API Key 未配置")
        self.api_key = api_key

    def get_balance(self) -> float:
        text = _call(self.api_key, {"action": "getBalance"})
        # ACCESS_BALANCE:1.788
        match = re.search(r"ACCESS_BALANCE:([\d.]+)", text)
        if match:
            return float(match.group(1))
        raise SmsBowerError(f"解析余额失败: {text}")

    def get_number(
        self,
        service: str,
        country: str = "6",
        max_price: float | None = None,
        min_price: float | None = None,
        operator: str | None = None,
        phone_exception: str | None = None,
        provider_ids: str | None = None,
        except_provider_ids: str | None = None,
        quality: str = "",
    ) -> SmsBowerNumber:
        params: dict = {"action": "getNumber", "service": service, "country": country}
        if max_price is not None:
            params["maxPrice"] = str(max_price)
        if min_price is not None:
            params["minPrice"] = str(min_price)
        if operator:
            params["operator"] = operator
        if phone_exception:
            params["phoneException"] = phone_exception
        if provider_ids:
            params["providerIds"] = provider_ids
        if except_provider_ids:
            params["exceptProviderIds"] = except_provider_ids
        if quality and quality.lower() in ("gold", "silver"):
            params["type"] = quality.lower()

        text = _call(self.api_key, params)
        if text.startswith("WRONG_EXCEPTION_PHONE"):
            raise SmsBowerInvalidPhoneExceptionError(text)
        # ACCESS_NUMBER:$activationId:$phoneNumber
        match = re.match(r"ACCESS_NUMBER:(\d+):(\d+)", text)
        if match:
            return SmsBowerNumber(
                activation_id=match.group(1),
                phone_number=match.group(2),
                service=service,
                country=country,
                quality=quality,
            )
        raise SmsBowerError(f"解析号码失败: {text}")

    def get_status(self, activation_id: str) -> tuple[str, str | None]:
        """返回 (status, code)。
        status: wait / ok / cancel / error
        code: 验证码（仅 ok 时有值）
        """
        text = _call(self.api_key, {"action": "getStatus", "id": activation_id})
        if text.startswith("STATUS_OK:"):
            code = text.split(":", 1)[1].strip()
            return ("ok", code)
        if text == "STATUS_WAIT_CODE":
            return ("wait", None)
        if text == "STATUS_WAIT_RETRY":
            return ("retry", None)
        if text == "STATUS_CANCEL":
            return ("cancel", None)
        raise SmsBowerError(f"未知状态: {text}")

    def set_status(self, activation_id: str, status: int) -> str:
        """设置激活状态。
        status: 1=ready(短信发送中), 3=retry, 6=complete(已收到码), 8=cancel
        """
        text = _call(self.api_key, {"action": "setStatus", "id": activation_id, "status": str(status)})
        return text

    def wait_for_code(
        self,
        activation_id: str,
        timeout: int = DEFAULT_TIMEOUT,
        interval: float = DEFAULT_POLL_INTERVAL,
        on_poll: Callable[[str, str | None], None] | None = None,
    ) -> str:
        """轮询等待验证码。

        对 HTTP 网络错误以及 SMSBOWER 服务端临时错误（如 ERROR_SQL、未知状态等）
        都进行容错，避免单次轮询失败导致整个 add-phone 流程被中止。
        """
        deadline = time.monotonic() + timeout
        transient_errors = 0
        while time.monotonic() < deadline:
            try:
                status, code = self.get_status(activation_id)
            except SmsBowerRequestError as exc:
                transient_errors += 1
                if on_poll:
                    on_poll("request_error", None)
                if transient_errors >= 12:
                    raise SmsBowerTimeoutError(
                        f"轮询连续 {transient_errors} 次网络错误: {exc}"
                    )
                time.sleep(min(interval, max(1.0, deadline - time.monotonic())))
                continue
            except SmsBowerError as exc:
                # ERROR_SQL / 未知状态 等服务端临时错误：不立即抛出，sleep 后重试
                transient_errors += 1
                if on_poll:
                    on_poll(f"smsbower_error:{exc}", None)
                if transient_errors >= 12:
                    raise SmsBowerTimeoutError(
                        f"轮询连续 {transient_errors} 次服务端错误: {exc}"
                    )
                time.sleep(min(interval, max(1.0, deadline - time.monotonic())))
                continue
            if on_poll:
                on_poll(status, code)
            if status == "ok" and code:
                # 即使 set_status(6) 失败也返回 code，避免漏码
                try:
                    self.set_status(activation_id, 6)
                except Exception:
                    pass
                return code
            if status == "retry":
                raise SmsBowerWaitRetryError("SMSBOWER requested retry for activation")
            if status == "cancel":
                raise SmsBowerError("激活已取消")
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        raise SmsBowerTimeoutError(f"等待验证码超时 ({timeout}s)")

    def cancel(self, activation_id: str) -> None:
        try:
            self.set_status(activation_id, 8)
        except Exception:
            pass

    def get_prices(self, service: str, country: str) -> dict:
        import json
        text = _call(self.api_key, {"action": "getPrices", "service": service, "country": country})
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
