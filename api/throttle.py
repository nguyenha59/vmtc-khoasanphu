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
