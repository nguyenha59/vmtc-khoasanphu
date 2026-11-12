"""Kênh SMS mặc định: xuất file Excel để tải lên WorldSMS."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pandas as pd

from core.channels.base import RowResult, SendResult, SmsRow
from core.config import DATA_DIR, TZ


class ExcelSmsChannel:
    name = "excel"

    def __init__(self, out_dir: Path | None = None) -> None:
        self.out_dir = out_dir or DATA_DIR / "exports"

    def send_batch(self, template_code: str, rows: list[SmsRow]) -> SendResult:
        keys: list[str] = []
        for r in rows:
            for k in r.params:
                if k not in keys:
                    keys.append(k)
        df = pd.DataFrame([{"Số điện thoại": r.phone, **r.params} for r in rows],
                          columns=["Số điện thoại", *keys])
        buf = io.BytesIO()
        df.astype(str).to_excel(buf, index=False)
        data = buf.getvalue()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{template_code}_{datetime.now(TZ):%Y-%m-%d_%H%M%S}.xlsx"
        path.write_bytes(data)
        return SendResult(channel=self.name, template=template_code,
                          rows=[RowResult(r.patient_code, "exported") for r in rows],
                          file_path=path, file_bytes=data)
