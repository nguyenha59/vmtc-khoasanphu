"""Che lại 'bằng chứng' đã lưu trong Assessment.findings trước bản sửa #5 (11/11/2026). Chạy lại nhiều lần không sao.

    python scripts/mask_stored_evidence.py            # có NER (chậm hơn, che cả tên người khác)
    python scripts/mask_stored_evidence.py --no-ner   # chỉ SĐT/CCCD/PID/email/tên thai phụ
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from core import audit  # noqa: E402
from core.ai.masking import default_ner, mask_fragment  # noqa: E402
from core.config import load_env  # noqa: E402
from core.db import session_scope  # noqa: E402
from core.models import Assessment, FormResponse, Patient  # noqa: E402
from core.repo import patient_name  # noqa: E402


def run(use_ner: bool) -> int:
    ner = default_ner() if use_ner else None
    changed = 0
    with session_scope() as s:
        rows = s.execute(select(Assessment, Patient).join(FormResponse, Assessment.response_id == FormResponse.id)
                         .join(Patient, FormResponse.patient_id == Patient.id)).all()
        for asm, p in rows:
            f = asm.findings or {}
            items = f.get("ai") or []
            if not items:
                continue
            name = patient_name(p)
            dirty = False
            for x in items:
                ev = x.get("evidence") or ""
                persons = ner(ev) if (ner and ev) else []
                new = mask_fragment(ev, full_name=name, pid=p.pid, persons=persons)
                if new != ev:
                    x["evidence"], dirty = new, True
            if dirty:
                asm.findings = f
                flag_modified(asm, "findings")
                changed += 1
        audit.log("mask_stored_evidence", "assessment", None, {"changed": changed, "ner": use_ner}, session=s)
    return changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ner", action="store_true")
    load_env()
    n = run(not ap.parse_args().no_ner)
    print(f"Đã che lại bằng chứng trong {n} đánh giá.")
