"""Đọc danh sách khách đến khám (docs/03 mục 1.2): pid, visit_date, checkin_time."""

from __future__ import annotations

from datetime import date, time
from typing import IO, Any

import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from core import audit
from core.db import now
from core.ingest.his_reader import parse_date, parse_time
from core.models import Arrival, ImportBatch


def read_arrivals(file: str | IO[bytes] | Any) -> list[tuple[str, date, time | None]]:
    name = str(getattr(file, "name", file)).lower()
    df = (pd.read_csv(file, dtype=str, keep_default_na=False) if name.endswith(".csv")
          else pd.read_excel(file, dtype=str, keep_default_na=False))
    missing = {"pid", "visit_date"} - set(df.columns)
    if missing:
        raise ValueError(f"Thiếu cột: {', '.join(sorted(missing))}")
    out, bad = [], []
    for i, r in enumerate(df.to_dict("records"), start=2):  # dòng 1 là tiêu đề
        if not any(str(v).strip() for v in r.values()):
            continue  # dòng trống hoàn toàn
        d = parse_date(r["visit_date"], "%Y-%m-%d") or parse_date(r["visit_date"], "%d/%m/%Y")
        if d is None or not str(r["pid"]).strip():
            bad.append(i)
            continue
        out.append((str(r["pid"]).strip(), d, parse_time(r.get("checkin_time", ""), "%H:%M")))
    # Nhập thay CẢ NGÀY: bỏ qua im lặng dòng hỏng sẽ xóa mất lượt đến đã có của khách ở dòng đó → không nhập gì.
    if bad:
        shown = ", ".join(map(str, bad[:10])) + ("…" if len(bad) > 10 else "")
        raise ValueError(f"Dòng {shown}: thiếu mã bệnh nhân hoặc ngày khám sai định dạng – không nhập file. "
                         "Sửa file rồi tải lại.")
    return out


def import_arrivals(s: Session, file: Any, file_name: str, user_id: int | None) -> int:
    rows = read_arrivals(file)
    batch = ImportBatch(kind="arrivals", file_name=file_name, row_count=len(rows), errors=[], created_by=user_id,
                        created_at=now())
    s.add(batch)
    s.flush()
    for d in {d for _, d, _ in rows}:
        s.execute(delete(Arrival).where(Arrival.visit_date == d))
    for pid, d, t in rows:
        s.add(Arrival(pid=pid, visit_date=d, checkin_time=t, batch_id=batch.id))
    audit.log("upload_arrivals", "import_batch", batch.id, {"rows": len(rows)}, user_id=user_id, session=s)
    return len(rows)
