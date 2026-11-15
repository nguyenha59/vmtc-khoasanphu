"""Form thai phụ (công khai, mở bằng token trong tin nhắn) + tạo đường dẫn demo cho nhân viên.

Không đăng nhập; form không hỏi tên, số điện thoại. AI không trả lời / tư vấn cho thai phụ.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import CurrentUser, require
from core import audit
from core.ai.llm_client import make_client_safe
from core.ai.question_selector import selector_client
from core.alerts import FormAlreadySubmitted, structured_red, submit_response
from core.channels import get_alert_channel
from core.config import FormsConfig, is_production, load_forms, load_rules
from core.db import now, session_scope
from core.forms import (
    check_token,
    compute_selection,
    create_token,
    is_demo_token,
    selection_for,
    snapshot_selection,
    token_form_codes,
)
from core.models import FormToken, Patient
from core.rules.form_scheduler import ga_at
from core.rules.personalize import compose_merged

router = APIRouter(prefix="/api", tags=["form-demo"])  # cần đăng nhập – chỉ ở máy chủ nội bộ
public_router = APIRouter(prefix="/api", tags=["public-form"])  # không đăng nhập – tiến trình công khai dùng riêng
YESNO = ["Không", "Có", "Không chắc"]  # "Không chắc" ≠ "Không" → tối thiểu vàng (rules.yaml forms.unsure_level)
MAX_ANSWERS = 64  # form dài nhất < 30 câu (kể cả EPDS)


def _clinic() -> dict[str, str]:
    c = load_rules()["clinic"]
    return {"name": c["name"], "hotline": c["hotline"], "emergency_number": c["emergency_number"]}


class DemoIn(BaseModel):
    code: str
    form: str


@router.get("/forms/catalog")
def catalog(user: CurrentUser = Depends(require("demo_form"))) -> dict:
    return {"forms": [{"code": k, "name": v["name"]} for k, v in load_forms().forms.items()]}


@router.post("/forms/demo-token")
def demo_token(body: DemoIn, user: CurrentUser = Depends(require("demo_form"))) -> dict:
    if is_production():
        raise HTTPException(403, "Form demo đã tắt khi dùng thật (APP_ENV=production).")
    rules, forms_cfg = load_rules(), load_forms()
    if body.form not in forms_cfg.forms:
        raise HTTPException(422, "Form không tồn tại.")
    with session_scope() as s:
        p = s.scalar(select(Patient).where(Patient.code == body.code.strip()))
        if p is None:
            raise HTTPException(404, "Không tìm thấy mã thai phụ.")
        if p.consent_status != "granted":
            raise HTTPException(409, "Thai phụ chưa đồng thuận – không gửi form được. Ghi nhận đồng thuận trong Hồ sơ theo dõi.")
        # Chọn câu (có thể gọi AI) TRƯỚC khi ghi token – không giữ khóa ghi trong lúc chờ AI.
        computed = compute_selection(s, p, [body.form], rules, forms_cfg, selector_client(rules))
        t = create_token(s, p.id, body.form, rules["forms"]["token_ttl_days"], demo=True)
        s.flush()
        snapshot_selection(s, p, t, [body.form], rules, forms_cfg, computed=computed)
        audit.log("create_demo_token", "patient", body.code.strip(), {"form": body.form}, user_id=user.user_id, session=s)
    return {"token": t, "path": f"/form?token={t}"}


@public_router.get("/public/form/{token}")
def get_form(token: str) -> dict:
    forms_cfg = load_forms()
    with session_scope() as s:
        t, err = check_token(s, token)
        p = s.get(Patient, t.patient_id) if t is not None and err is None else None
        if err or p is None:
            raise HTTPException(404, {"message": err or "Đường dẫn không hợp lệ.", "clinic": _clinic()})
        code, form_code = p.code, t.form_code
        sel = selection_for(s, token)
        qcodes = [x["code"] for x in sel if x["code"] in forms_cfg.questions] if sel else _questions_for(s, p, t, forms_cfg)
        group_label = {x["code"]: x.get("group_label") for x in sel or []}
        names = [forms_cfg.forms[c]["name"] for c in token_form_codes(s, t) if c in forms_cfg.forms]
    fdef = forms_cfg.forms[form_code]
    questions = []
    for q in qcodes:
        qd = forms_cfg.questions[q]
        questions.append({"key": q, "type": qd["type"], "text": qd["text"], "min": qd.get("min"),
                          "max": qd.get("max"), "step": qd.get("step", 1), "group": group_label.get(q)})
    epds = None
    if fdef.get("epds"):
        epds = {"options": forms_cfg.epds["options"],
                "items": [{"key": k, "text": v} for k, v in forms_cfg.epds["items"].items()]}
    return {"code": code, "form": form_code, "name": " + ".join(names), "period": fdef.get("period"),
            "questions": questions, "epds": epds, "clinic": _clinic(), "yesno": YESNO}


def _questions_for(s: Any, p: Patient, t: FormToken, forms_cfg: FormsConfig) -> list[str]:
    """Câu hỏi của đường dẫn cho ĐÚNG thai phụ này: form chính + form gộp cùng ngày + bộ câu theo tình trạng
    điều dưỡng đã xác nhận; mỗi câu một lần. Tuần thai tính tại ngày tạo đường dẫn → mở lại vẫn cùng bộ câu."""
    ga = ga_at(p.edd, t.created_at.date())
    return compose_merged(token_form_codes(s, t), list(p.risk_groups or []), ga, load_rules(), forms_cfg)


class SubmitIn(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    free_text: str = Field("", max_length=2000)


@public_router.post("/public/form/{token}")
async def submit(token: str, body: SubmitIn) -> dict:
    if len(body.answers) > MAX_ANSWERS:
        raise HTTPException(422, "Quá nhiều câu trả lời.")
    rules, forms_cfg = load_rules(), load_forms()
    with session_scope() as s:
        t, err = check_token(s, token)
        if err or t is None:
            raise HTTPException(409, {"message": err or "Đường dẫn không hợp lệ.", "clinic": _clinic()})
        form_code = t.form_code
        p0 = s.get(Patient, t.patient_id)
        sel = selection_for(s, token)
        if sel:
            allowed = {x["code"] for x in sel}
        else:
            allowed = set(_questions_for(s, p0, t, forms_cfg)) if p0 is not None else set()
    fdef = forms_cfg.forms[form_code]
    epds_keys = set(forms_cfg.epds["items"]) if fdef.get("epds") else set()
    answers: dict[str, object] = {}
    for k, v in body.answers.items():  # chỉ nhận câu thuộc form, đúng kiểu
        if k in allowed and k in forms_cfg.questions:  # câu đã bỏ khỏi forms.yaml sau khi gửi link → bỏ qua
            qd = forms_cfg.questions[k]
            if qd["type"] == "yesno" and v in YESNO:
                answers[k] = v
            elif qd["type"] == "number" and isinstance(v, int | float):
                if not float(qd.get("min", 0)) <= float(v) <= float(qd.get("max", 999)):
                    raise HTTPException(422, f"Giá trị ngoài khoảng cho phép: {qd['text']}")
                answers[k] = float(v)
        elif k in epds_keys and isinstance(v, int) and 0 <= v < len(forms_cfg.epds["options"]):
            answers[k] = v

    def work() -> tuple[str, str]:
        # AI hỏng / sai cấu hình → None: quy tắc vẫn chạy, phần tự viết dò theo danh mục và tối thiểu mức vàng.
        with session_scope() as s:
            t2, err2 = check_token(s, token)
            if err2 or t2 is None:
                raise HTTPException(409, {"message": err2 or "Đường dẫn không hợp lệ.", "clinic": _clinic()})
            p = s.get(Patient, t2.patient_id)
            # Không kiểm tra mạng/Ollama khi quy tắc đã đủ để cảnh báo đỏ.
            client, _why = (None, None) if structured_red(p, answers, now(), rules) else make_client_safe(rules)
            _r, asm, _a, _res = submit_response(s, p, form_code, answers, body.free_text, rules=rules, forms_cfg=forms_cfg,
                                                client=client, alert_channel=get_alert_channel(rules), token=token,
                                                source="demo" if is_demo_token(token) else "app")
            return asm.level, p.code

    try:
        level, code = await run_in_threadpool(work)
    except FormAlreadySubmitted as e:
        raise HTTPException(409, {"message": "Form này đã được gửi trước đó. Cảm ơn chị!", "clinic": _clinic()}) from e
    return {"level": level, "code": code, "reminder": fdef.get("reminder"), "clinic": _clinic()}
