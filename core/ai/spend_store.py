"""Atomic daily spend and reservations, serialized across threads and processes."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_THREAD_LOCK = threading.RLock()


@contextmanager
def locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, path.with_suffix(path.suffix + ".lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + 10
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Không lấy được khóa sổ chi phí") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("days", {}), dict) or not isinstance(data.get("pending", {}), dict):
        raise ValueError("Sổ chi phí sai cấu trúc")
    for row in [*data.get("days", {}).values(), *data.get("pending", {}).values()]:
        if not isinstance(row, dict) or not isinstance(row.get("usd"), (int, float)) or not math.isfinite(row["usd"]) or row["usd"] < 0:
            raise ValueError("Chi phí không hợp lệ")
    return data


def write(path: Path, data: dict[str, Any]) -> None:
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            name = stream.name
            json.dump(data, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def add(data: dict[str, Any], day: str, usd: float, tokens_in: int, tokens_out: int) -> float:
    row = data.setdefault("days", {}).setdefault(day, {"usd": 0.0, "tokens_in": 0, "tokens_out": 0, "calls": 0})
    row["usd"] += usd
    row["tokens_in"] += tokens_in
    row["tokens_out"] += tokens_out
    row["calls"] += 1
    return float(row["usd"])


def reserve(path: Path, day: str, estimated: float, budget: float) -> str | None:
    with locked(path):
        data = read(path)
        spent = float(data.get("days", {}).get(day, {}).get("usd", 0.0))
        pending = sum(float(r["usd"]) for r in data.get("pending", {}).values() if r["day"] == day)
        if spent + pending + estimated > budget:
            return None
        token = uuid.uuid4().hex
        data.setdefault("pending", {})[token] = {"usd": estimated, "day": day}
        write(path, data)
        return token


def settle(path: Path, token: str, *, usage: tuple[float, int, int] | None, charged: bool) -> None:
    with locked(path):
        data = read(path)
        row = data.get("pending", {}).pop(token, None)
        if row is None:
            return
        if charged:
            # Không có usage sau khi đã gửi: giữ mức dự phòng để không chi lại phần chưa rõ.
            add(data, row["day"], *(usage or (row["usd"], 0, 0)))
        write(path, data)
