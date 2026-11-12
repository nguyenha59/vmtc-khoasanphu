"""Chặn đoán mật khẩu: sai MAX_FAILS lần trong WINDOW (theo IP + tên đăng nhập) → khóa tạm đến hết cửa sổ.

Lưu trong bộ nhớ của tiến trình (một tiến trình nội bộ – đủ cho MVP). Khởi động lại máy chủ thì xóa bộ đếm.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

MAX_FAILS = 5
WINDOW_SECONDS = 15 * 60


class LoginThrottle:
    def __init__(self, max_fails: int = MAX_FAILS, window: float = WINDOW_SECONDS) -> None:
        self.max_fails, self.window = max_fails, window
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def key(ip: str, username: str) -> str:
        return f"{ip}|{username.strip().lower()}"

    def _trim(self, q: deque[float], t: float) -> None:
        while q and q[0] <= t - self.window:
            q.popleft()

    def retry_after(self, key: str, t: float | None = None) -> int:
        """Số giây còn bị khóa (0 = được thử)."""
        t = time.monotonic() if t is None else t
        with self._lock:
            q = self._fails.get(key)
            if not q:
                return 0
            self._trim(q, t)
            return int(q[0] + self.window - t) + 1 if len(q) >= self.max_fails else 0

    def failed(self, key: str, t: float | None = None) -> None:
        t = time.monotonic() if t is None else t
        with self._lock:
            q = self._fails[key]
            self._trim(q, t)
            q.append(t)

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


login_throttle = LoginThrottle()
# Dò mật khẩu nhiều tên đăng nhập từ cùng một IP (password spraying): tổng số lần sai theo IP.
login_ip_throttle = LoginThrottle(max_fails=20, window=WINDOW_SECONDS)


class RateLimiter:
    """Giới hạn số yêu cầu theo khóa (IP) trong cửa sổ trượt. Dùng cho trang form công khai (mở ra Internet)."""

    def __init__(self, max_requests: int, window: float) -> None:
        self.max_requests, self.window = max_requests, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, t: float | None = None) -> int:
        """Ghi một yêu cầu. Trả số giây phải chờ (0 = cho qua)."""
        t = time.monotonic() if t is None else t
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= t - self.window:
                q.popleft()
            if len(q) >= self.max_requests:
                return int(q[0] + self.window - t) + 1
            q.append(t)
            if len(self._hits) > 50_000:  # chặn phình bộ nhớ khi bị quét nhiều IP
                for k in [k for k, v in self._hits.items() if not v][:10_000]:
                    self._hits.pop(k, None)
            return 0


# Thai phụ mở form + gửi: vài yêu cầu. 60 yêu cầu / 10 phút / IP là dư cho người thật, chặn quét / gọi dồn AI.
public_limiter = RateLimiter(max_requests=60, window=10 * 60)
