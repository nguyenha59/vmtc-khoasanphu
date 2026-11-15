"""Opt-in second-pass ML review: synthetic data, temporary artifacts only.

Run: .venv\\Scripts\\python.exe -m pytest tests/ai_deep_ml_repros.py -ra --tb=short
Assertions express desired behavior; failures document unresolved findings.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from core.ai import noshow


def _visits(n_days: int = 30, per_day: int = 80) -> pd.DataFrame:
    """Synthetic perfectly learnable signal, unique patient per appointment."""
    rows = []
    start = date(2026, 1, 1)
    for day_no in range(n_days):
        day = start + timedelta(days=day_no)
        for j in range(per_day):
            aid = day_no * per_day + j + 1
            high = j % 2 == 1
            rows.append({
                "appointment_id": aid, "patient_id": aid, "pid": f"SYNTH-{aid}",
                "code": f"SYNTH-{aid}", "appt_date": day, "start_time": time(9),
                "booking_source": "MyVinmec" if high else "Bác sĩ hẹn",
                "booked_at": datetime.combine(day - timedelta(days=30 if high else 1), time(9)),
                "services": "Khám thai", "nationality": "Việt Nam",
                "edd": day + timedelta(days=100), "label": int(high),
            })
    return pd.DataFrame(rows)


def test_ml01_existing_model_with_empty_database_returns_empty_list(db, monkeypatch):
    from api.routers import noshow as api_noshow

    monkeypatch.setattr(api_noshow, "load_latest", lambda: {"version": "synthetic-audit-only"})
    response = api_noshow._compute(date(2026, 11, 15))
    assert response["rows"] == [] and response["n_appt"] == 0


def test_ml02_same_day_unknown_label_must_not_enter_prior_history():
    visits = _visits(2, 2)
    visits.loc[0:1, ["pid", "patient_id"]] = ["SYNTH-SAME-PATIENT", 1]
    visits.loc[0, "start_time"] = time(8)
    visits.loc[1, "start_time"] = time(10)
    visits.loc[0, "label"] = 1
    feats = noshow.build_features(visits)
    second = feats.loc[feats["appointment_id"] == 2].iloc[0]
    assert second["prior_noshows"] == 0 and second["prior_rate"] == 0, {
        "prior_noshows": second["prior_noshows"], "prior_rate": second["prior_rate"],
        "first_visit": second["first_visit"],
    }


def test_ml03_single_class_test_set_must_not_publish_nan_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(noshow, "MODELS_DIR", tmp_path)
    visits = _visits()
    test_start = date(2026, 1, 21)
    visits.loc[visits["appt_date"] >= test_start, "label"] = 0
    try:
        result = noshow.train(noshow.build_features(visits))
    except ValueError:
        return  # Rejecting an unevaluable split is also safe.
    json.dumps(result.metrics, allow_nan=False)


def test_ml04_single_class_calibration_must_not_silence_all_risk(tmp_path, monkeypatch):
    monkeypatch.setattr(noshow, "MODELS_DIR", tmp_path)
    visits = _visits()
    # Default 30-day split: train Jan 1-20, calibrate Jan 16-20, test Jan 21-30.
    cal = (visits["appt_date"] >= date(2026, 1, 16)) & (visits["appt_date"] <= date(2026, 1, 20))
    visits.loc[cal, "label"] = 0
    feats = noshow.build_features(visits)
    try:
        result = noshow.train(feats)
    except ValueError:
        return  # Insufficient class support should be a controlled readiness failure.
    bundle = noshow.load_latest()
    test = feats[feats["appt_date"] >= date(2026, 1, 21)]
    raw = bundle["model"].predict_proba(test[noshow.FEATURES].astype(float))[:, 1]
    prob = bundle["calibrator"].predict(raw)
    base_rate = float(test["label"].mean())
    brier = float(np.mean((prob - test["label"].to_numpy()) ** 2))
    assert np.any(prob > 0), {
        "published_raw_auc": result.metrics["lightgbm"]["roc_auc"],
        "calibrated_probability_unique": np.unique(prob).tolist(),
        "test_positive_count": int(test["label"].sum()), "test_base_rate": base_rate,
        "calibrated_brier": brier,
        "constant_prevalence_brier": float(np.mean((base_rate - test["label"].to_numpy()) ** 2)),
    }


def test_control_balanced_calibration_produces_nonzero_risks(tmp_path, monkeypatch):
    monkeypatch.setattr(noshow, "MODELS_DIR", tmp_path)
    feats = noshow.build_features(_visits())
    result = noshow.train(feats)
    json.dumps(result.metrics, allow_nan=False)
    bundle = noshow.load_latest()
    raw = bundle["model"].predict_proba(feats[noshow.FEATURES].astype(float))[:, 1]
    assert np.any(bundle["calibrator"].predict(raw) > 0)


def test_control_distinct_days_only_use_earlier_labels():
    visits = _visits(3, 1)
    visits["pid"] = "SYNTH-SAME-PATIENT"
    visits["label"] = [1, 0, 1]
    feats = noshow.build_features(visits)
    assert feats["prior_noshows"].tolist() == [0, 1, 1]
    assert feats["prior_rate"].tolist() == [0.0, 1.0, 0.5]
