"""Dự đoán bỏ hẹn (docs/05 mục 6): LightGBM + Logistic Regression, isotonic, SHAP top 3."""

from __future__ import annotations

import pickle
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import ROOT, Rules
from core.models import Appointment, Arrival, Patient
from core.textutil import word_pattern

MODELS_DIR = ROOT / "models"
FEATURES = ["lead_days", "source_app", "dow", "afternoon", "svc_us", "svc_lab", "svc_exam", "ga_weeks",
            "prior_noshows", "prior_rate", "first_visit", "foreign"]
DOW_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật"]


# ---------------------------------------------------------------- dữ liệu
def load_visits(s: Session) -> pd.DataFrame:
    """Mỗi dòng = một lịch hẹn (đã gộp theo khách/ngày) kèm nhãn đến/không đến nếu có danh sách khách đến."""
    rows = s.execute(select(Appointment, Patient).join(Patient, Appointment.patient_id == Patient.id)).all()
    data = [{
        "appointment_id": a.id, "patient_id": p.id, "pid": p.pid, "code": p.code, "appt_date": a.appt_date,
        "start_time": a.start_time, "booking_source": a.booking_source or "", "booked_at": a.booked_at,
        "services": " | ".join(a.services or []), "nationality": p.nationality or "", "edd": p.edd,
    } for a, p in rows]
    df = pd.DataFrame(data)
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
    df = df.sort_values(["appt_date", "start_time"]).reset_index(drop=True)
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
    for pid, lab in zip(df["pid"], df["label"], strict=False):
        n, ns = seen.get(pid, (0, 0))
        prior_ns.append(ns)
        prior_n.append(n)
        first.append(int(n == 0))
        if not pd.isna(lab):
            seen[pid] = (n + 1, ns + int(lab))
        else:
            seen[pid] = (n, ns)
    out["prior_noshows"] = prior_ns
    out["prior_rate"] = [ns / n if n else 0.0 for ns, n in zip(prior_ns, prior_n, strict=False)]
    out["first_visit"] = first
    out["foreign"] = (df["nationality"] != "Việt Nam").astype(int)
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
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=False):
        m = (p >= lo) & (p <= hi)
        if m.sum():
            out.append({"pred": float(p[m].mean()), "obs": float(y[m].mean()), "n": int(m.sum())})
    return out


@dataclass
class TrainResult:
    version: str
    metrics: dict[str, Any]
    path: Path


def next_version() -> str:
    MODELS_DIR.mkdir(exist_ok=True)
    nums = [int(m.group(1)) for p in MODELS_DIR.glob("noshow_v*.pkl") if (m := re.search(r"v(\d+)", p.stem))]
    return f"v{max(nums, default=0) + 1}"


def train(feats: pd.DataFrame, train_days: int = 60, seed: int = 42) -> TrainResult:
    import lightgbm as lgb
    from sklearn.impute import SimpleImputer
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    lab = feats[feats["label"].notna()].copy()
    days = sorted(lab["appt_date"].unique())
    if len(days) < 20:
        raise ValueError("Cần ít nhất 20 ngày có danh sách khách đến để huấn luyện.")
    split = days[min(train_days, len(days) - 10) - 1]
    tr, te = lab[lab["appt_date"] <= split], lab[lab["appt_date"] > split]
    tr_days = sorted(tr["appt_date"].unique())
    cal_split = tr_days[int(len(tr_days) * 0.75) - 1]
    fit, cal = tr[tr["appt_date"] <= cal_split], tr[tr["appt_date"] > cal_split]
    X = lambda d: d[FEATURES].astype(float)  # noqa: E731
    y = lambda d: d["label"].astype(int).to_numpy()  # noqa: E731

    def make_gbm() -> lgb.LGBMClassifier:
        return lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=4, min_child_samples=100,
                                  subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0,
                                  random_state=seed, verbose=-1)

    # Hiệu chỉnh isotonic trên phần cuối cửa sổ huấn luyện (giữ thứ tự thời gian),
    # mô hình xếp hạng cuối cùng học trên toàn bộ 60 ngày.
    early = make_gbm().fit(X(fit), y(fit))
    iso = IsotonicRegression(out_of_bounds="clip").fit(early.predict_proba(X(cal))[:, 1], y(cal))
    gbm = make_gbm().fit(X(tr), y(tr))
    lr = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=1000))
    lr.fit(X(tr), y(tr))

    p_gbm = iso.predict(gbm.predict_proba(X(te))[:, 1])
    p_raw = gbm.predict_proba(X(te))[:, 1]
    p_lr = lr.predict_proba(X(te))[:, 1]
    yt = y(te)
    metrics = {
        "n_train": int(len(tr)), "n_test": int(len(te)), "base_rate_test": float(yt.mean()),
        "train_until": str(split), "test_from": str(te["appt_date"].min()), "test_to": str(te["appt_date"].max()),
        "lightgbm": {"roc_auc": float(roc_auc_score(yt, p_raw)), "pr_auc": float(average_precision_score(yt, p_raw)),
                     "precision_top10": _precision_at(yt, p_gbm, 0.10)},
        "logistic": {"roc_auc": float(roc_auc_score(yt, p_lr)), "pr_auc": float(average_precision_score(yt, p_lr)),
                     "precision_top10": _precision_at(yt, p_lr, 0.10)},
        "calibration": _calibration(yt, p_gbm),
    }
    version = next_version()
    path = MODELS_DIR / f"noshow_{version}.pkl"
    with open(path, "wb") as f:
        pickle.dump({"model": gbm, "calibrator": iso, "logistic": lr, "features": FEATURES, "version": version,
                     "metrics": metrics, "trained_at": datetime.now().isoformat(timespec="seconds")}, f)
    return TrainResult(version=version, metrics=metrics, path=path)


def load_latest() -> dict[str, Any] | None:
    paths = sorted(MODELS_DIR.glob("noshow_v*.pkl"), key=lambda p: int(re.search(r"v(\d+)", p.stem).group(1)))  # type: ignore[union-attr]
    if not paths:
        return None
    with open(paths[-1], "rb") as f:
        return pickle.load(f)


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
    import shap

    X = feats[bundle["features"]].astype(float)
    raw = bundle["model"].predict_proba(X)[:, 1]
    prob = bundle["calibrator"].predict(raw)
    sv = shap.TreeExplainer(bundle["model"]).shap_values(X)
    if isinstance(sv, list):
        sv = sv[1]
    sv = np.asarray(sv)
    if sv.ndim == 3:
        sv = sv[:, :, 1]
    factors = []
    for i in range(len(X)):
        order = np.argsort(-sv[i])[:top_k]
        factors.append([{"feature": bundle["features"][j], "shap": float(sv[i, j]),
                         "text": factor_text(bundle["features"][j], float(X.iloc[i, j]))} for j in order])
    return pd.DataFrame({"appointment_id": feats["appointment_id"].values, "prob": prob, "top_factors": factors})


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
