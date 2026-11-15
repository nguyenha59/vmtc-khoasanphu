"""Dự đoán bỏ hẹn (docs/05 mục 6): LightGBM + Logistic Regression, isotonic, SHAP top 3."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import ROOT, TZ, Rules
from core.models import Appointment, Arrival, Patient
from core.textutil import word_pattern

MODELS_DIR = ROOT / "models"
FEATURES = ["lead_days", "source_app", "dow", "afternoon", "svc_us", "svc_lab", "svc_exam", "ga_weeks",
            "prior_noshows", "prior_rate", "first_visit", "foreign"]
DOW_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật"]
FEATURE_VERSION = "noshow-patient-day-v2"
FEATURE_COLUMNS = [*FEATURES, "appointment_id", "appt_date", "label"]
VISIT_COLUMNS = ["appointment_id", "patient_id", "pid", "code", "appt_date", "start_time",
                 "booking_source", "booked_at", "services", "nationality", "edd", "label"]
log = logging.getLogger(__name__)


class ModelNotReady(ValueError):
    """Dữ liệu / mô hình chưa qua kiểm chứng; không thay mô hình đang dùng."""


@dataclass
class SigmoidCalibrator:
    """Ánh xạ logistic một chiều; giữ giao diện predict(scores) như isotonic."""

    estimator: Any

    def predict(self, scores: np.ndarray) -> np.ndarray:
        return self.estimator.predict_proba(np.asarray(scores).reshape(-1, 1))[:, 1]


# ---------------------------------------------------------------- dữ liệu
def load_visits(s: Session) -> pd.DataFrame:
    """Mỗi dòng = một lịch hẹn (đã gộp theo khách/ngày) kèm nhãn đến/không đến nếu có danh sách khách đến."""
    rows = s.execute(select(Appointment, Patient).join(Patient, Appointment.patient_id == Patient.id)).all()
    data = [{
        "appointment_id": a.id, "patient_id": p.id, "pid": p.pid, "code": p.code, "appt_date": a.appt_date,
        "start_time": a.start_time, "booking_source": a.booking_source or "", "booked_at": a.booked_at,
        "services": " | ".join(a.services or []), "nationality": p.nationality or "", "edd": p.edd,
    } for a, p in rows]
    df = pd.DataFrame(data, columns=VISIT_COLUMNS)
    if df.empty:
        return df
    arrivals = pd.DataFrame(s.execute(select(Arrival.pid, Arrival.visit_date)).all(), columns=["pid", "visit_date"])
    # Chỉ gán nhãn cho NGÀY đã nhập danh sách khách đến. Trước đây mọi ngày ≤ ngày nhập cuối đều được gán nhãn
    # → ngày chưa nhập thì cả ngày bị tính là bỏ hẹn (sai dữ liệu huấn luyện và đặc trưng "đã bỏ hẹn N lần").
    labeled_days = set(arrivals["visit_date"])
    came = set(zip(arrivals["pid"], arrivals["visit_date"], strict=False))
    df["label"] = [
        (0 if (pid, d) in came else 1) if d in labeled_days else np.nan
        for pid, d in zip(df["pid"], df["appt_date"], strict=False)
    ]
    return df


def arrivals_imported(s: Session, day: date) -> bool:
    """Đã nhập danh sách khách đến của ngày này chưa (có ít nhất một lượt đến)."""
    return s.scalar(select(Arrival.id).where(Arrival.visit_date == day).limit(1)) is not None


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=FEATURE_COLUMNS)
    df = df.sort_values(["appt_date", "start_time"], kind="stable").reset_index(drop=True)
    out = pd.DataFrame(index=df.index)
    booked = pd.to_datetime(df["booked_at"])
    appt = pd.to_datetime(df["appt_date"])
    out["lead_days"] = (appt - booked.dt.normalize()).dt.days.fillna(0).clip(lower=0)
    # Khách tự đặt: "Autobooking" (tiền tố ghi chú trong file HIS) hoặc "MyVinmec" (dữ liệu lịch sử). Trống = bác sĩ hẹn.
    out["source_app"] = df["booking_source"].fillna("").str.contains("autobooking|myvinmec", case=False).astype(int)
    out["dow"] = appt.dt.weekday
    out["afternoon"] = df["start_time"].map(lambda t: int(t >= time(12, 0)))
    svc = df["services"].str.lower()
    out["svc_us"] = svc.str.contains("siêu âm").astype(int)
    out["svc_lab"] = svc.str.contains("xét nghiệm|nghiệm pháp").astype(int)
    out["svc_exam"] = svc.str.contains("khám").astype(int)
    edd = pd.to_datetime(df["edd"])
    ga = ((280 - (edd - appt).dt.days) / 7).where(edd.notna())
    out["ga_weeks"] = ga.where((ga >= 4) & (ga <= 44))
    # lịch sử bỏ hẹn TRƯỚC ngày hẹn (không rò rỉ nhãn tương lai)
    prior_ns, prior_n, first = [], [], []
    seen: dict[str, tuple[int, int]] = {}
    for _day, rows in df.groupby("appt_date", sort=False):
        # Cả ngày dùng cùng snapshot cuối ngày trước; mỗi PID/ngày chỉ là một lượt.
        for pid in rows["pid"]:
            n, ns = seen.get(pid, (0, 0))
            prior_ns.append(ns)
            prior_n.append(n)
            first.append(int(n == 0))
        for pid, visits in rows.groupby("pid", sort=False):
            labels = visits["label"].dropna()
            if not labels.empty:
                n, ns = seen.get(pid, (0, 0))
                # Có bất kỳ lượt đến nào trong ngày thì không coi cả ngày là bỏ hẹn.
                seen[pid] = (n + 1, ns + int(labels.min()))
    out["prior_noshows"] = prior_ns
    out["prior_rate"] = [ns / n if n else 0.0 for ns, n in zip(prior_ns, prior_n, strict=False)]
    out["first_visit"] = first
    from core.rules.export_worldsms import is_vietnamese

    out["foreign"] = (~df["nationality"].fillna("").map(is_vietnamese)).astype(int)
    out["appointment_id"] = df["appointment_id"].values
    out["appt_date"] = df["appt_date"].values
    out["label"] = df["label"].values
    return out


# ---------------------------------------------------------------- huấn luyện
def _precision_at(y: np.ndarray, p: np.ndarray, frac: float) -> float:
    k = max(1, int(len(p) * frac))
    idx = np.argsort(-p)[:k]
    return float(y[idx].mean())


def _calibration(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    """Bảng dự đoán vs thực tế theo nhóm phân vị. Mỗi ca thuộc đúng MỘT nhóm (tổng n = số ca test)."""
    edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, max(len(edges) - 2, 0))
    out = []
    for b in range(max(len(edges) - 1, 1)):
        m = idx == b
        if m.sum():
            out.append({"pred": float(p[m].mean()), "obs": float(y[m].mean()), "n": int(m.sum())})
    return out


@dataclass
class TrainResult:
    version: str
    metrics: dict[str, Any]
    path: Path


def next_version() -> str:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for p in MODELS_DIR.glob("noshow_v*.pkl") if (m := re.search(r"v(\d+)", p.stem))]
    return f"v{max(nums, default=0) + 1}"


def train(feats: pd.DataFrame, train_days: int = 60, seed: int = 42) -> TrainResult:
    import lightgbm as lgb
    import sklearn
    from sklearn.impute import SimpleImputer
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    missing = set(FEATURE_COLUMNS) - set(feats.columns)
    if missing:
        raise ModelNotReady("Thiếu dữ liệu đặc trưng để huấn luyện mô hình bỏ hẹn.")
    lab = feats[feats["label"].notna()].copy()
    if train_days < 2 or not set(lab["label"].unique()) <= {0, 1}:
        raise ModelNotReady("Cửa sổ huấn luyện hoặc nhãn bỏ hẹn không hợp lệ.")
    if lab["appt_date"].isna().any() or np.isinf(lab[FEATURES].astype(float).to_numpy()).any():
        raise ModelNotReady("Ngày hẹn / đặc trưng huấn luyện không hợp lệ.")
    days = sorted(lab["appt_date"].unique())
    if len(days) < 20:
        raise ModelNotReady("Cần ít nhất 20 ngày có danh sách khách đến để huấn luyện.")
    split = days[min(train_days, len(days) - 10) - 1]
    tr, te = lab[lab["appt_date"] <= split], lab[lab["appt_date"] > split]
    tr_days = sorted(tr["appt_date"].unique())
    cal_split = tr_days[int(len(tr_days) * 0.75) - 1]
    fit, cal = tr[tr["appt_date"] <= cal_split], tr[tr["appt_date"] > cal_split]
    class_counts = {}
    for name, part in (("fit", fit), ("calibration", cal), ("test", te)):
        counts = part["label"].value_counts()
        if not {0, 1} <= set(counts.index):
            raise ModelNotReady(f"Phần {name} cần có cả khách đến và khách bỏ hẹn; giữ mô hình hiện tại.")
        class_counts[name] = {str(c): int(counts[c]) for c in (0, 1)}
    X = lambda d: d[FEATURES].astype(float)  # noqa: E731
    y = lambda d: d["label"].astype(int).to_numpy()  # noqa: E731

    def make_gbm() -> lgb.LGBMClassifier:
        return lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=4, min_child_samples=100,
                                  subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0,
                                  random_state=seed, verbose=-1)

    # Giữ đúng estimator đã tạo điểm calibration; không refit bằng nhãn calibration.
    gbm = make_gbm().fit(X(fit), y(fit))
    cal_scores = _probabilities(gbm.predict_proba(X(cal))[:, 1], len(cal))
    if np.ptp(cal_scores) <= 1e-12:
        raise ModelNotReady("Mô hình không phân biệt được nguy cơ trên phần hiệu chỉnh.")
    if len(cal) >= 1000:
        method = "isotonic"
        calibrator = IsotonicRegression(out_of_bounds="clip").fit(cal_scores, y(cal))
    else:
        method = "sigmoid"
        sigmoid = LogisticRegression(random_state=seed, max_iter=1000)
        sigmoid.fit(cal_scores.reshape(-1, 1), y(cal))
        calibrator = SigmoidCalibrator(sigmoid)
    lr = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=1000))
    lr.fit(X(fit), y(fit))

    p_raw = _probabilities(gbm.predict_proba(X(te))[:, 1], len(te))
    p_gbm = _probabilities(calibrator.predict(p_raw), len(te))
    p_lr = _probabilities(lr.predict_proba(X(te))[:, 1], len(te))
    if np.ptp(p_gbm) <= 1e-12:
        raise ModelNotReady("Xác suất đã hiệu chỉnh không phân biệt được nguy cơ; giữ mô hình hiện tại.")
    yt = y(te)

    def metrics_for(prob: np.ndarray) -> dict[str, float]:
        return {"roc_auc": float(roc_auc_score(yt, prob)),
                "pr_auc": float(average_precision_score(yt, prob)),
                "precision_top10": _precision_at(yt, prob, 0.10),
                "brier": float(brier_score_loss(yt, prob)),
                "log_loss": float(log_loss(yt, prob, labels=[0, 1]))}

    operational = metrics_for(p_gbm)
    metrics = {
        "n_train": int(len(tr)), "n_test": int(len(te)), "base_rate_test": float(yt.mean()),
        "train_until": str(split), "test_from": str(te["appt_date"].min()), "test_to": str(te["appt_date"].max()),
        "n_fit": int(len(fit)), "n_calibration": int(len(cal)),
        "fit_until": str(cal_split), "calibration_from": str(cal["appt_date"].min()),
        "calibration_to": str(cal["appt_date"].max()), "calibration_method": method,
        "class_counts": class_counts,
        "lightgbm": operational, "lightgbm_raw": metrics_for(p_raw),
        "lightgbm_calibrated": operational.copy(), "logistic": metrics_for(p_lr),
        "calibration": _calibration(yt, p_gbm),
    }
    try:
        json.dumps(metrics, allow_nan=False)
    except (ValueError, TypeError) as e:
        raise ModelNotReady("Chỉ số đánh giá mô hình không hữu hạn.") from e
    bundle = {"model": gbm, "calibrator": calibrator, "logistic": lr, "features": FEATURES,
              "feature_version": FEATURE_VERSION, "metrics": metrics,
              "trained_at": datetime.now(TZ).isoformat(timespec="seconds"),
              "validation": {"status": "passed", "schema_version": 2, "class_counts": class_counts},
              "data_sha256": hashlib.sha256(pd.util.hash_pandas_object(lab, index=False).values.tobytes()).hexdigest(),
              "library_versions": {"lightgbm": lgb.__version__, "sklearn": sklearn.__version__,
                                   "numpy": np.__version__, "pandas": pd.__version__}}
    # Chỉ công bố file hoàn chỉnh sau mọi kiểm chứng. Hard link tạo tên mới nguyên tử,
    # không ghi đè bản khác khi nhiều worker cùng huấn luyện.
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        version = next_version()
        bundle["version"] = version
        validate_bundle(bundle)
        with tempfile.NamedTemporaryFile(dir=MODELS_DIR, prefix=".noshow_", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            try:
                pickle.dump(bundle, f)
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                f.close()
                temporary.unlink(missing_ok=True)
                raise
        path = MODELS_DIR / f"noshow_{version}.pkl"
        try:
            os.link(temporary, path)
        except FileExistsError:
            continue
        finally:
            temporary.unlink(missing_ok=True)
        return TrainResult(version=version, metrics=metrics, path=path)


def load_latest() -> dict[str, Any] | None:
    paths = [p for p in MODELS_DIR.glob("noshow_v*.pkl") if re.fullmatch(r"noshow_v\d+", p.stem)]
    paths.sort(key=lambda p: int(p.stem.removeprefix("noshow_v")), reverse=True)
    for path in paths:
        try:
            with open(path, "rb") as f:
                bundle = pickle.load(f)
            validate_bundle(bundle)
            return bundle
        except (OSError, ValueError, TypeError, AttributeError, ImportError, EOFError, pickle.UnpicklingError):
            log.warning("Bỏ qua mô hình bỏ hẹn chưa qua kiểm chứng: %s", path.name)
    return None


def _probabilities(values: Any, n: int) -> np.ndarray:
    try:
        p = np.asarray(values, dtype=float)
    except (ValueError, TypeError) as e:
        raise ModelNotReady("Mô hình bỏ hẹn trả xác suất sai kiểu.") from e
    if p.shape != (n,) or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ModelNotReady("Mô hình bỏ hẹn trả xác suất không hợp lệ.")
    return p


def validate_bundle(bundle: Any) -> None:
    if not isinstance(bundle, dict) or bundle.get("feature_version") != FEATURE_VERSION:
        raise ModelNotReady("Cần huấn luyện lại mô hình bỏ hẹn theo bộ đặc trưng đã kiểm chứng.")
    if not isinstance(bundle.get("version"), str) or not re.fullmatch(r"v[1-9]\d*", bundle["version"]):
        raise ModelNotReady("Phiên bản mô hình bỏ hẹn không hợp lệ.")
    validation = bundle.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "passed" or validation.get("schema_version") != 2:
        raise ModelNotReady("Mô hình bỏ hẹn chưa qua kiểm chứng.")
    for name in ("fit", "calibration", "test"):
        support = validation.get("class_counts")
        counts = support.get(name, {}) if isinstance(support, dict) else {}
        if not isinstance(counts, dict):
            raise ModelNotReady("Dữ liệu kiểm chứng mô hình bỏ hẹn không hợp lệ.")
        if any(not isinstance(counts.get(c), int) or counts[c] < 1 for c in ("0", "1")):
            raise ModelNotReady("Mô hình bỏ hẹn thiếu dữ liệu cho một trong hai lớp.")
    if bundle.get("features") != FEATURES or not callable(getattr(bundle.get("model"), "predict_proba", None)) \
            or not callable(getattr(bundle.get("calibrator"), "predict", None)):
        raise ModelNotReady("Tệp mô hình bỏ hẹn không tương thích.")
    try:
        metrics = bundle["metrics"]
        json.dumps(metrics, allow_nan=False)
        for name in ("lightgbm", "lightgbm_raw", "lightgbm_calibrated", "logistic"):
            for key in ("roc_auc", "pr_auc", "brier", "log_loss", "precision_top10"):
                value = metrics[name][key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                    raise ValueError("non-finite model metric")
    except (ValueError, KeyError, TypeError) as e:
        raise ModelNotReady("Chỉ số mô hình bỏ hẹn thiếu hoặc không hữu hạn.") from e


# ---------------------------------------------------------------- dự đoán + giải thích
def factor_text(name: str, value: float) -> str:
    if name == "lead_days":
        return f"Đặt lịch {value:.0f} ngày trước"
    if name == "source_app":
        return "Đặt qua app" if value else "Bác sĩ hẹn"
    if name == "dow":
        return f"Hẹn vào {DOW_VI[int(value)]}"
    if name == "afternoon":
        return "Khung giờ chiều" if value else "Khung giờ sáng"
    if name == "prior_noshows":
        return f"Đã bỏ hẹn {value:.0f} lần trước" if value else "Chưa từng bỏ hẹn"
    if name == "prior_rate":
        return f"Tỷ lệ bỏ hẹn trước đây {value:.0%}"
    if name == "first_visit":
        return "Lần khám đầu" if value else "Khách tái khám"
    if name == "foreign":
        return "Khách nước ngoài" if value else "Khách trong nước"
    if name == "ga_weeks":
        return "Chưa rõ tuần thai" if np.isnan(value) else f"Tuần thai {value:.0f}"
    if name == "svc_us":
        return "Có siêu âm" if value else "Không siêu âm"
    if name == "svc_lab":
        return "Có xét nghiệm" if value else "Không xét nghiệm"
    if name == "svc_exam":
        return "Có khám" if value else "Không khám"
    return f"{name}={value}"


def predict(bundle: dict[str, Any], feats: pd.DataFrame, top_k: int = 3) -> pd.DataFrame:
    if feats.empty:
        return pd.DataFrame(columns=["appointment_id", "prob", "score", "top_factors"])
    try:
        validate_bundle(bundle)
        X = feats[bundle["features"]].astype(float)
        outputs = np.asarray(bundle["model"].predict_proba(X), dtype=float)
        if outputs.shape != (len(X), 2):
            raise ModelNotReady("Mô hình bỏ hẹn trả sai số lớp / số ca.")
        raw = _probabilities(outputs[:, 1], len(X))
        prob = _probabilities(bundle["calibrator"].predict(raw), len(X))
    except ModelNotReady:
        raise
    except Exception as e:
        raise ModelNotReady("Không chạy được mô hình bỏ hẹn; cần kiểm tra hoặc huấn luyện lại.") from e
    factors: list[list[dict[str, Any]]] = [[] for _ in range(len(X))]
    try:
        import shap

        sv = shap.TreeExplainer(bundle["model"]).shap_values(X)
        if isinstance(sv, list):
            sv = sv[1]
        sv = np.asarray(sv)
        if sv.ndim == 3:
            sv = sv[:, :, 1]
        if sv.shape != X.shape or not np.isfinite(sv).all():
            raise ValueError("invalid SHAP output")
        for i in range(len(X)):
            order = np.argsort(-sv[i])[:max(0, top_k)]
            factors[i] = [{"feature": bundle["features"][j], "shap": float(sv[i, j]),
                           "text": factor_text(bundle["features"][j], float(X.iloc[i, j]))} for j in order]
    except Exception as e:
        # Giải thích là tác vụ phụ: lỗi SHAP không làm mất dự đoán đã kiểm chứng.
        log.warning("Không tạo được giải thích mô hình bỏ hẹn: %s", type(e).__name__)
    # score = điểm thô của mô hình: isotonic gom xác suất thành ít mức (≈50 mức / 3.000 ca) → dùng score để xếp
    # thứ tự các ca cùng mức (danh sách gọi), prob vẫn là xác suất đã hiệu chỉnh để so ngưỡng.
    return pd.DataFrame({"appointment_id": feats["appointment_id"].values, "prob": prob, "score": raw,
                         "top_factors": factors})


def action_for(prob: float, rules: Rules) -> str:
    cfg = rules["noshow"]
    if prob >= cfg["call_threshold"]:
        return "Gọi xác nhận"
    if prob >= cfg["sms_threshold"]:
        return "Gửi thêm SMS"
    return "—"


# ---------------------------------------------------------------- mời đặt lại
def priority_label(ga: tuple[int, int] | None, services: list[str], rules: Rules) -> str | None:
    """Mốc sàng lọc có cửa sổ hẹp (11–13+6, NPDN 24–28, liên cầu B 35–37)."""
    text = " ".join(services)
    for w in rules["noshow"]["priority_windows"]:
        if any(word_pattern(k).search(text) for k in w.get("keywords", [])):
            return w["label"]
    for w in rules["noshow"]["priority_windows"]:
        if ga is not None and tuple(w["ga_from"]) <= ga <= tuple(w["ga_to"]):
            return w["label"]
    return None


def reinvite_list(s: Session, day: date, rules: Rules) -> list[dict[str, Any]]:
    from core.repo import ga_on, patient_phone
    from core.security import mask_phone

    arrived = {pid for (pid,) in s.execute(select(Arrival.pid).where(Arrival.visit_date == day))}
    if not arrived:  # chưa nhập danh sách khách đến ngày này → không biết ai vắng, không mời nhầm người đã đến
        return []
    rows = s.execute(select(Appointment, Patient).join(Patient, Appointment.patient_id == Patient.id)
                     .where(Appointment.appt_date == day)).all()
    out = []
    for a, p in rows:
        if p.pid in arrived:
            continue
        ga = ga_on(p, day)
        label = priority_label(ga, list(a.services or []), rules)
        out.append({"patient_id": p.id, "code": p.code, "pid": p.pid, "phone": patient_phone(p),
                    "phone_masked": mask_phone(patient_phone(p)), "appt": f"{a.appt_date:%d/%m} {a.start_time:%H:%M}",
                    "services": ", ".join(a.services or []), "ga": f"{ga[0]}+{ga[1]}" if ga else "—",
                    "priority": label or "", "is_priority": label is not None})
    out.sort(key=lambda r: (not r["is_priority"], r["code"] or ""))
    return out
