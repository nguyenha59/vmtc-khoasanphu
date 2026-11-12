"""Mức cảnh báo: xanh < vàng < đỏ."""

from __future__ import annotations

from enum import IntEnum


class Level(IntEnum):
    GREEN = 0
    YELLOW = 1
    RED = 2

    @property
    def code(self) -> str:
        return {0: "green", 1: "yellow", 2: "red"}[int(self)]

    @property
    def label(self) -> str:
        return {0: "Xanh", 1: "Vàng", 2: "Đỏ"}[int(self)]

    @classmethod
    def parse(cls, value: str | int | Level | None) -> Level:
        if value is None:
            return cls.GREEN
        if isinstance(value, Level):
            return value
        if isinstance(value, int):
            return cls(value)
        return {"green": cls.GREEN, "yellow": cls.YELLOW, "red": cls.RED}[str(value).lower()]


def max_level(*levels: Level | str | None) -> Level:
    return max((Level.parse(x) for x in levels), default=Level.GREEN)


LEVEL_COLORS = {"red": "#B42318", "yellow": "#8A5100", "green": "#1E7B34"}
