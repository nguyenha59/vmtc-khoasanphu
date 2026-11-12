from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from core.ai import noshow
from core.ai.noshow import action_for, build_features, factor_text, predict, priority_label, train


def fake_visits(n_days: int = 90, per_day: int = 60, seed: int = 1) -> pd.DataFrame:
    rng = random.Random(seed)
    rows, aid = [], 0
    start = date(2026, 6, 1)
    habit = {f"P{i}": rng.random() < 0.15 for i in range(800)}
    for d in range(n_days):
        day = start + timedelta(days=d)
        for _ in range(per_day):
            pid = f"P{rng.randrange(800)}"
            lead = rng.randint(1, 40)
            st = time(rng.choice([7, 8, 9, 10, 13, 14, 15]), 0)
            logit = -3 + 1.2 * (lead > 21) + 1.5 * habit[pid] + 1.0 * (day.weekday() == 2 and st.hour >= 12)
            label = int(rng.random() < 1 / (1 + np.exp(-logit)))
            aid += 1
            rows.append({"appointment_id": aid, "patient_id": int(pid[1:]), "pid": pid, "code": pid,
                         "appt_date": day, "start_time": st, "booking_source": rng.choice(["Bác sĩ hẹn", "MyVinmec"]),
                         "booked_at": datetime.combine(day - timedelta(days=lead), time(9)),
                         "services": rng.choice(["Khám thai", "Siêu âm 2D", "Xét nghiệm máu"]),
                         "nationality": "Việt Nam", "edd": day + timedelta(days=100), "label": float(label)})
    return pd.DataFrame(rows)


def test_features_no_leakage():
    v = fake_visits(5, 30)
    f = build_features(v)
    assert set(noshow.FEATURES) <= set(f.columns)
    first = f.groupby(v.sort_values(["appt_date", "start_time"]).reset_index(drop=True)["pid"]).head(1)
    assert (first["prior_noshows"] == 0).all() and (first["first_visit"] == 1).all()


def test_train_time_split_and_lgbm_beats_logistic(tmp_path, monkeypatch):
    monkeypatch.setattr(noshow, "MODELS_DIR", tmp_path)
    res = train(build_features(fake_visits()))
    m = res.metrics
    assert pd.Timestamp(m["test_from"]) > pd.Timestamp(m["train_until"])  # chia theo thời gian
    assert m["lightgbm"]["roc_auc"] > 0.6
    assert m["lightgbm"]["roc_auc"] >= m["logistic"]["roc_auc"] - 0.01
    assert res.path.exists()


def test_predict_three_readable_factors(tmp_path, monkeypatch):
    monkeypatch.setattr(noshow, "MODELS_DIR", tmp_path)
    feats = build_features(fake_visits())
    train(feats)
    bundle = noshow.load_latest()
    out = predict(bundle, feats.tail(20))
    assert ((out["prob"] >= 0) & (out["prob"] <= 1)).all()
    for factors in out["top_factors"]:
        assert len(factors) == 3
        assert all(isinstance(f["text"], str) and f["text"] for f in factors)


def test_factor_text_vietnamese():
    assert factor_text("lead_days", 28) == "Đặt lịch 28 ngày trước"
    assert factor_text("source_app", 1) == "Đặt qua app"
    assert factor_text("dow", 2) == "Hẹn vào Thứ Tư"


def test_action_thresholds(rules):
    assert action_for(0.6, rules) == "Gọi xác nhận"
    assert action_for(0.4, rules) == "Gửi thêm SMS"
    assert action_for(0.1, rules) == "—"


def test_priority_windows(rules):
    assert priority_label((12, 2), ["Siêu âm độ mờ da gáy (NT)"], rules) == "sàng lọc 11-13+6"
    assert priority_label((25, 0), ["Khám thai"], rules) == "nghiệm pháp đường huyết 24-28"
    assert priority_label((30, 0), ["Khám thai"], rules) is None
    assert priority_label(None, ["Xét nghiệm liên cầu B"], rules) == "liên cầu B 35-37"


@pytest.mark.parametrize("prob", [0.0, 1.0])
def test_action_edges(rules, prob):
    assert action_for(prob, rules) in ("—", "Gọi xác nhận")
