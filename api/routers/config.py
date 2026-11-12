"""Màn Cấu hình quy tắc: đề xuất (điều dưỡng/bác sĩ), duyệt (bác sĩ), khóa an toàn, kênh gửi tin (admin)."""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import CurrentUser, current_user, require
from api.util import jsonable
from core import audit
from core.config import load_his_columns, load_rules
from core.db import session_scope
from core.models import RuleChange, User
from core.rule_config import RuleChangeError, approve, list_rules, propose, reject
from core.security import PermissionDenied, can

router = APIRouter(prefix="/api/config", tags=["config"])
GROUPS = {"arrival": "Giờ đến", "forms": "Mốc gửi form", "thresholds": "Ngưỡng cảnh báo", "channels": "Kênh gửi tin"}
CHANNEL_CHOICES = {"channels.sms": ["excel", "mock", "test_api"], "channels.alert": ["mock", "teams_webhook"]}


@router.get("")
def config(user: CurrentUser = Depends(require("view_config"))) -> dict:
    rules = load_rules()
    role = user.role
    with session_scope() as s:
        audit.log("view_config", "rules", rules.rules_version, user_id=user.user_id, session=s)
        rows = list_rules(s, rules)
        names = {u.id: u.name for u in s.scalars(select(User))}
        pending = [{"id": c.id, "key": c.key, "old": jsonable(c.old_value), "new": jsonable(c.new_value), "reason": c.reason,
                    "by": names.get(c.proposed_by, "?"), "at": c.proposed_at.isoformat() if c.proposed_at else None}
                   for c in s.scalars(select(RuleChange).where(RuleChange.status == "pending").order_by(RuleChange.proposed_at))]
        history = [{"key": c.key, "status": c.status, "version": c.new_rules_version,
                    "at": c.decided_at.isoformat() if c.decided_at else None, "by": names.get(c.decided_by or 0, "")}
                   for c in s.scalars(select(RuleChange).where(RuleChange.status != "pending")
                                      .order_by(RuleChange.decided_at.desc()).limit(20))]

    def editable(r: Any) -> bool:
        return not r.locked and not r.pending and (can(role, "change_channel") if r.admin_only else can(role, "propose_rule"))

    his = load_his_columns()
    return {
        "rules_version": rules.rules_version,
        "can_approve": can(role, "approve_rule"),
        "groups": [{"key": g, "label": label, "rules": [
            {"key": r.key, "value": jsonable(r.value), "basis": r.basis, "requires_doctor": r.requires_doctor,
             "locked": r.locked, "pending": r.pending, "editable": editable(r), "choices": CHANNEL_CHOICES.get(r.key)}
            for r in rows if r.group == g]} for g, label in GROUPS.items()],
        "pending": pending, "history": history,
        "his": {"columns": [{"name": k, "field": v, "required": v in his.required} for k, v in his.columns.items()],
                "date_format": his.date_format, "time_format": his.time_format},
    }


class ProposeIn(BaseModel):
    key: str
    value: str
    reason: str = ""


@router.post("/propose")
def do_propose(body: ProposeIn, user: CurrentUser = Depends(current_user)) -> dict:
    rules = load_rules()
    try:
        value = body.value if body.key in CHANNEL_CHOICES else yaml.safe_load(body.value)
        with session_scope() as s:
            ch = propose(s, body.key, value, body.reason, role=user.role, user_id=user.user_id, rules=rules)
            status = ch.status
    except PermissionDenied as e:
        raise HTTPException(403, str(e)) from e
    except (RuleChangeError, yaml.YAMLError) as e:
        raise HTTPException(422, str(e)) from e
    return {"status": status}


@router.post("/changes/{change_id}/approve")
def do_approve(change_id: int, user: CurrentUser = Depends(require("approve_rule"))) -> dict:
    try:
        with session_scope() as s:
            v = approve(s, change_id, role=user.role, user_id=user.user_id, rules=load_rules())
    except PermissionDenied as e:
        raise HTTPException(403, str(e)) from e
    except RuleChangeError as e:
        raise HTTPException(422, str(e)) from e
    return {"rules_version": v}


@router.post("/changes/{change_id}/reject")
def do_reject(change_id: int, user: CurrentUser = Depends(require("approve_rule"))) -> dict:
    with session_scope() as s:
        reject(s, change_id, role=user.role, user_id=user.user_id)
    return {"ok": True}
