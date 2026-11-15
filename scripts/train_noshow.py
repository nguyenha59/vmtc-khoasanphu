"""Huấn luyện mô hình dự đoán bỏ hẹn từ CSDL (lịch hẹn + danh sách khách đến).

python scripts/train_noshow.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_env, load_rules  # noqa: E402


def run(user_id: int | None = None) -> dict:
    from core import audit
    from core.ai.evaluate import save_eval
    from core.ai.noshow import ModelNotReady, build_features, load_visits, train
    from core.db import init_db, session_scope

    init_db()
    with session_scope() as s:
        feats = build_features(load_visits(s))
    try:
        res = train(feats)
    except ModelNotReady as e:
        audit.log("train_noshow_rejected", "model", None, {"reason": str(e)}, user_id=user_id)
        raise
    with session_scope() as s:
        save_eval(s, "noshow", {**res.metrics, "model_version": res.version}, load_rules(), f"lightgbm-{res.version}", None)
        audit.log("train_noshow", "model", res.version, {"roc_auc": res.metrics["lightgbm"]["roc_auc"]},
                  user_id=user_id, session=s)
    return {"version": res.version, **res.metrics}


if __name__ == "__main__":
    from core.ai.noshow import ModelNotReady

    load_env()
    try:
        out = run()
    except ModelNotReady as e:
        raise SystemExit(f"Chưa xuất mô hình mới: {e}") from e
    out.pop("calibration", None)
    print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))
