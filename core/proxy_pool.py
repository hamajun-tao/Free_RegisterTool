"""代理池 - 从数据库读取代理，支持轮询和按区域选取"""

from typing import Optional
from sqlmodel import Session, select
from .db import ProxyModel, engine
from .proxy_utils import build_requests_proxy_config
import time, threading, random
from datetime import datetime, timezone


# 默认代理冷却时间（秒）：同一 IP 两次注册之间的最小间隔
# [风控降维打击策略引入]：OpenAI 会在 24 小时后批量封禁同一 IP (或紧凑 IP 字符串) 聚集注册的账号。
# 为了抵御基于时序和批次的 Probe 打标，冷却时间被大幅拉长至 2 小时（7200秒）。严禁发生秒级连发！
DEFAULT_PROXY_COOLDOWN_SECONDS: float = 7200.0  # 2 小时


class ProxyPool:
    def __init__(self, cooldown_seconds: float = DEFAULT_PROXY_COOLDOWN_SECONDS):
        self._index = 0
        self._lock = threading.Lock()
        # per-proxy 冷却追踪：proxy_url -> 上次使用的 timestamp
        self._last_used: dict[str, float] = {}
        self._cooldown_seconds: float = cooldown_seconds

    def set_cooldown(self, seconds: float) -> None:
        """动态调整冷却时间（秒）"""
        with self._lock:
            self._cooldown_seconds = max(0.0, seconds)

    def mark_used(self, url: str) -> None:
        """标记代理已用于注册，开始冷却计时"""
        if not url:
            return
        with self._lock:
            self._last_used[url] = time.time()

    def _is_cooling_down(self, url: str) -> bool:
        """检查代理是否仍在冷却期"""
        last = self._last_used.get(url)
        if last is None:
            return False
        return (time.time() - last) < self._cooldown_seconds

    def get_next(self, region: str = "", respect_cooldown: bool = True) -> Optional[str]:
        """加权轮询取一个可用代理，在高成功率代理间轮换。

        Args:
            region: 可选的区域过滤
            respect_cooldown: 是否遵守 per-proxy 冷却机制。
                              设为 False 可忽略冷却（如紧急情况）。
        """
        with Session(engine) as s:
            q = select(ProxyModel).where(ProxyModel.is_active == True)
            if region:
                q = q.where(ProxyModel.region == region)
            proxies = s.exec(q).all()
            if not proxies:
                return None
            proxies.sort(
                key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1),
                reverse=True,
            )

            # 如果启用冷却，过滤掉仍在冷却中的代理
            if respect_cooldown and self._cooldown_seconds > 0:
                available = [p for p in proxies if not self._is_cooling_down(p.url)]
                # 如果全部都在冷却中，找最早结束冷却的那个
                if not available:
                    proxies.sort(
                        key=lambda p: self._last_used.get(p.url, 0),
                    )
                    # 返回冷却时间最长的（最早使用的）
                    available = proxies[:1]
                proxies = available

            if not proxies:
                return None

            with self._lock:
                idx = self._index % len(proxies)
                self._index += 1
            return proxies[idx].url

    def get_cooldown_status(self) -> dict:
        """获取所有代理的冷却状态摘要"""
        now = time.time()
        with self._lock:
            status = {}
            for url, last in self._last_used.items():
                remaining = max(0, self._cooldown_seconds - (now - last))
                status[url] = {
                    "last_used": last,
                    "remaining_seconds": round(remaining, 1),
                    "cooling_down": remaining > 0,
                }
            return status

    def report_success(self, url: str) -> None:
        with Session(engine) as s:
            p = s.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
            if p:
                p.success_count += 1
                p.last_checked = datetime.now(timezone.utc)
                s.add(p)
                s.commit()

    def report_fail(self, url: str) -> None:
        with Session(engine) as s:
            p = s.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
            if p:
                p.fail_count += 1
                p.last_checked = datetime.now(timezone.utc)
                # 连续失败超过10次自动禁用
                if p.fail_count > 0 and p.success_count == 0 and p.fail_count >= 5:
                    p.is_active = False
                s.add(p)
                s.commit()

    def check_all(self) -> dict:
        """检测所有代理可用性"""
        import requests

        with Session(engine) as s:
            proxies = s.exec(select(ProxyModel)).all()
        results = {"ok": 0, "fail": 0}
        for p in proxies:
            try:
                r = requests.get(
                    "https://api.ipify.org?format=json",
                    proxies=build_requests_proxy_config(p.url),
                    timeout=8,
                )
                if r.status_code == 200:
                    self.report_success(p.url)
                    results["ok"] += 1
                    continue
            except Exception:
                pass
            self.report_fail(p.url)
            results["fail"] += 1
        return results


proxy_pool = ProxyPool()
