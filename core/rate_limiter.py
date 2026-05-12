"""线程安全的滑动窗口限流器。"""

import threading
import time


class RateLimiter:
    """线程安全的滑动窗口限流器。

    使用示例::

        # 每 3 秒最多 1 次调用
        limiter = RateLimiter(max_calls=1, period_seconds=3.0)
        limiter.acquire()  # 阻塞直到获取令牌
    """

    def __init__(self, max_calls: int, period_seconds: float):
        if max_calls < 1:
            raise ValueError("max_calls 必须 >= 1")
        if period_seconds <= 0:
            raise ValueError("period_seconds 必须 > 0")
        self._max_calls = max_calls
        self._period = float(period_seconds)
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """阻塞直到获取令牌，返回是否成功。

        Args:
            timeout: 最大等待秒数，None 表示无限等待。

        Returns:
            True 表示成功获取令牌，False 表示超时。
        """
        deadline = time.monotonic() + (timeout or float("inf"))
        while True:
            with self._lock:
                now = time.monotonic()
                # 清理过期时间戳
                self._timestamps = [
                    t for t in self._timestamps if now - t < self._period
                ]
                if len(self._timestamps) < self._max_calls:
                    self._timestamps.append(now)
                    return True
                # 计算需要等待的时间
                wait = self._period - (now - self._timestamps[0])

            if timeout is not None and time.monotonic() + wait > deadline:
                return False
            time.sleep(min(max(wait, 0.01), 0.1))  # 分段 sleep，下限 0.01s

    def try_acquire(self) -> bool:
        """非阻塞尝试获取令牌。"""
        return self.acquire(timeout=0)

    @property
    def available(self) -> int:
        """当前可用令牌数（仅供调试）。"""
        with self._lock:
            now = time.monotonic()
            self._timestamps = [
                t for t in self._timestamps if now - t < self._period
            ]
            return max(0, self._max_calls - len(self._timestamps))
