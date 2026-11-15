"""Model readiness, point-in-time features, and failure-safe no-show inference."""

from __future__ import annotations

import json
import pickle
import sys
from datetime import date, time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from test_m6_noshow import fake_visits

from core.ai import noshow


@pytest.fixture()
def candidate_data():
    return noshow.build_features(fake_visits(n_days=40, per_day=40))


@pytest.fixture()
def active_model(tmp_path, monkeypatch, candidate_data):
    monkeypatch.setattr(noshow, "MODELS_DIR", tmp_path)
    result = noshow.train(candidate_data)
    return result, noshow.load_latest()


def test_empty_database_and_empty_features_remain_usable(db, monkeypatch):
    from api.routers import noshow as api_noshow

    with db.session_scope() as s:
        visits = noshow.load_visits(s)
    feats = noshow.build_features(visits)
    assert feats.empty and set(noshow.FEATURE_COLUMNS) <= set(feats.columns)
    monkeypatch.setattr(api_noshow, "load_latest", lambda: {"version": "old-placeholder"})
    result = api_noshow._compute(date(2026, 11, 15))
    assert result["rows"] == [] and result["n_appt"] == 0
    assert result["bundle"] is None and result["model_warning"]


def test_same_day_history_is_not_label_dependent():
    visits = fake_visits(3, 1)
    visits["pid"] = "SYNTH-PATIENT"
    visits["label"] = [1, 0, 1]
    visits.loc[1, "appt_date"] = visits.loc[0, "appt_date"]
    visits.loc[0, "start_time"], visits.loc[1, "start_time"] = time(8), time(10)
    before = noshow.build_features(visits)
    assert before["prior_noshows"].tolist() == [0, 0, 0]
    assert before["prior_rate"].tolist() == [0.0, 0.0, 0.0]
    assert before["first_visit"].tolist() == [1, 1, 0]
    visits.loc[0:1, "label"] = 1
    after = noshow.build_features(visits)
    pd.testing.assert_frame_equal(before.iloc[:2][noshow.FEATURES], after.iloc[:2][noshow.FEATURES])
    assert after.iloc[:2]["prior_noshows"].tolist() == [0, 0]
    assert after.iloc[2]["prior_noshows"] == 1  # Same PID/day is one previous visit.
    assert after.iloc[2]["prior_rate"] == 1.0


@pytest.mark.parametrize("part", ["fit", "calibration", "test"])
def test_missing_class_does_not_replace_active_model(active_model, candidate_data, part):
    result, bundle = active_model
    previous = result.path.read_bytes()
    metrics = result.metrics
    modified = candidate_data.copy()
    if part == "fit":
        mask = modified["appt_date"] <= date.fromisoformat(metrics["fit_until"])
    elif part == "calibration":
        mask = (modified["appt_date"] >= date.fromisoformat(metrics["calibration_from"])) & (
            modified["appt_date"] <= date.fromisoformat(metrics["calibration_to"])
        )
    else:
        mask = modified["appt_date"] >= date.fromisoformat(metrics["test_from"])
    modified.loc[mask, "label"] = 0
    with pytest.raises(noshow.ModelNotReady, match=part):
        noshow.train(modified)
    assert result.path.read_bytes() == previous
    assert noshow.load_latest()["version"] == bundle["version"]
    assert len(list(result.path.parent.glob("noshow_v*.pkl"))) == 1


def test_calibration_labels_do_not_refit_base_estimator(active_model, candidate_data):
    first, original = active_model
    modified = candidate_data.copy()
    cal = modified[modified["appt_date"] >= date.fromisoformat(first.metrics["calibration_from"])]
    cal = cal[cal["appt_date"] <= date.fromisoformat(first.metrics["calibration_to"])]
    zero, one = cal[cal["label"] == 0].index[0], cal[cal["label"] == 1].index[0]
    modified.loc[zero, "label"], modified.loc[one, "label"] = 1, 0
    noshow.train(modified)
    updated = noshow.load_latest()
    X = candidate_data[noshow.FEATURES].astype(float)
    np.testing.assert_allclose(
        original["model"].predict_proba(X), updated["model"].predict_proba(X)
    )
    metrics = updated["metrics"]
    assert (
        metrics["fit_until"]
        < metrics["calibration_from"]
        <= metrics["calibration_to"]
        < metrics["test_from"]
    )
    assert metrics["lightgbm"] == metrics["lightgbm_calibrated"]
    assert set(metrics["lightgbm_raw"]) >= {"brier", "log_loss", "roc_auc", "pr_auc"}
    json.dumps(metrics, allow_nan=False)


def test_incomplete_or_nonfinite_newest_artifact_keeps_valid_model(active_model):
    result, original = active_model
    (result.path.parent / "noshow_v2.pkl").write_bytes(b"partial-pickle")
    invalid = {
        **original,
        "version": "v3",
        "metrics": {**original["metrics"], "lightgbm": {"roc_auc": float("nan")}},
    }
    (result.path.parent / "noshow_v3.pkl").write_bytes(pickle.dumps(invalid))
    assert noshow.load_latest()["version"] == original["version"]


def test_interrupted_publish_leaves_no_partial_model(active_model, candidate_data, monkeypatch):
    result, original = active_model

    def interrupted(_bundle, stream):
        stream.write(b"unfinished")
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(noshow.pickle, "dump", interrupted)
    with pytest.raises(OSError, match="synthetic storage failure"):
        noshow.train(candidate_data)
    assert noshow.load_latest()["version"] == original["version"]
    assert list(result.path.parent.glob("*.tmp")) == []
    assert len(list(result.path.parent.glob("noshow_v*.pkl"))) == 1


def test_invalid_runtime_probability_is_rejected(active_model, candidate_data):
    _, original = active_model
    invalid = {
        **original,
        "calibrator": SimpleNamespace(predict=lambda raw: np.full(len(raw), np.nan)),
    }
    with pytest.raises(noshow.ModelNotReady, match="xác suất"):
        noshow.predict(invalid, candidate_data.tail(3))


def test_runtime_failure_returns_model_warning_instead_of_writing_predictions(
    active_model, db, monkeypatch
):
    from api.routers import noshow as api_noshow
    from core.models import Appointment, NoshowPrediction
    from core.repo import upsert_patient

    _, original = active_model
    invalid = {
        **original,
        "calibrator": SimpleNamespace(predict=lambda raw: np.full(len(raw), np.nan)),
    }
    monkeypatch.setattr(api_noshow, "load_latest", lambda: invalid)
    target = date(2026, 11, 15)
    with db.session_scope() as s:
        p = upsert_patient(s, "SYNTH-ONLY", "Người Giả Lập", "0900000000", "Việt Nam")
        s.add(
            Appointment(
                patient_id=p.id, appt_date=target, start_time=time(9), services=["Khám thai"]
            )
        )
    result = api_noshow._compute(target)
    assert result["bundle"] is None and result["rows"] == [] and result["model_warning"]
    with db.session_scope() as s:
        assert s.query(NoshowPrediction).count() == 0


def test_retrain_readiness_returns_conflict(active_model, monkeypatch):
    import asyncio

    from fastapi import HTTPException

    from api.routers import noshow as api_noshow
    from scripts import train_noshow

    _, original = active_model

    def not_ready(_user_id):
        raise noshow.ModelNotReady("synthetic split missing a class")

    monkeypatch.setattr(train_noshow, "run", not_ready)
    with pytest.raises(HTTPException) as err:
        asyncio.run(api_noshow.retrain(SimpleNamespace(user_id=1)))
    assert err.value.status_code == 409
    assert noshow.load_latest()["version"] == original["version"]


def test_unrankable_candidate_keeps_active_model(active_model, candidate_data):
    _, original = active_model
    constant = candidate_data.copy()
    constant[noshow.FEATURES] = 0.0
    with pytest.raises(noshow.ModelNotReady, match="phân biệt"):
        noshow.train(constant)
    assert noshow.load_latest()["version"] == original["version"]


def test_shap_failure_keeps_valid_prediction(active_model, candidate_data, monkeypatch):
    _, bundle = active_model

    def unavailable(_model):
        raise ValueError("synthetic SHAP failure")

    monkeypatch.setitem(sys.modules, "shap", SimpleNamespace(TreeExplainer=unavailable))
    prediction = noshow.predict(bundle, candidate_data.tail(3))
    assert len(prediction) == 3 and np.isfinite(prediction["prob"]).all()
    assert prediction["top_factors"].tolist() == [[], [], []]
