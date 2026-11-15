"""Thay đổi quy tắc (docs/04 F): đề xuất → bác sĩ duyệt → ghi rules.yaml, tăng rules_version."""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml as safe_yaml
from ruamel.yaml import YAML
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import audit
from core.config import Rules, load_rules
from core.db import now
from core.models import RuleChange
from core.security import PermissionDenied, can, require


class RuleChangeError(ValueError):
    pass


@dataclass
class RuleRow:
    key: str
    value: Any
    group: str
    basis: str
    editable: bool
    requires_doctor: bool
    admin_only: bool
    locked: bool
    pending: bool


def _get(data: dict[str, Any], key: str) -> Any:
    node: Any = data
    for part in key.split("."):
        node = node[part]
    return node


def is_locked(rules: Rules, key: str) -> bool:
    meta = rules.get("rule_meta", {}).get(key)
    if meta is None or not meta.get("editable", False):
        return True
    try:
        v = _get(rules.data, key)
    except (KeyError, TypeError):
        return True
    return isinstance(v, dict) and bool(v.get("locked"))


def list_rules(s: Session, rules: Rules) -> list[RuleRow]:
    pending = {k for (k,) in s.execute(select(RuleChange.key).where(RuleChange.status == "pending"))}
    out = []
    for key, meta in rules.get("rule_meta", {}).items():
        try:
            value = _get(rules.data, key)
        except (KeyError, TypeError):
            continue
        out.append(RuleRow(key=key, value=value, group=meta.get("group", ""), basis=meta.get("basis", ""),
                           editable=bool(meta.get("editable")), requires_doctor=bool(meta.get("requires_doctor")),
                           admin_only=bool(meta.get("admin_only")), locked=is_locked(rules, key), pending=key in pending))
    return out


def _same_shape(old: Any, new: Any) -> bool:
    if old is None:
        return isinstance(new, (int, float)) or new is None
    if isinstance(old, bool) or isinstance(new, bool):
        return isinstance(old, bool) and isinstance(new, bool)
    if isinstance(old, (int, float)):
        return isinstance(new, (int, float))
    if isinstance(old, dict):
        return isinstance(new, dict) and set(old) == set(new) and all(_same_shape(old[k], new[k]) for k in old)
    if isinstance(old, list):
        return isinstance(new, list)
    return type(old) is type(new)


def bump_minor(version: str) -> str:
    major, minor, *_ = (version.split(".") + ["0", "0"])[:3]
    return f"{int(major)}.{int(minor) + 1}.0"


_LINE_RE = re.compile(r"^(?P<head>\s*[^#:\s][^:#]*:[ \t]*)(?P<val>[^#\r\n]*?)(?P<comment>[ \t]*#.*?)?(?P<nl>\r?\n)?$")


def _yaml_scalar(value: Any) -> str:
    """Giá trị một dòng hợp lệ YAML (JSON là tập con của YAML)."""
    return json.dumps(value, ensure_ascii=False)


def _edit_line(text: str, line_no: int, value: Any) -> str | None:
    """Thay giá trị trên đúng một dòng, giữ thụt lề và chú thích. None nếu không làm được."""
    lines = text.splitlines(keepends=True)
    if line_no >= len(lines):
        return None
    m = _LINE_RE.match(lines[line_no])
    if m is None:
        return None
    head, old_val = m.group("head"), m.group("val")
    comment, nl = m.group("comment") or "", m.group("nl") or ""
    if old_val.strip().startswith(("|", ">")):
        return None  # khối nhiều dòng
    new_val = _yaml_scalar(value)
    if not head.endswith((" ", "\t")):
        head += " "
    if comment:
        # giữ cột bắt đầu chú thích nếu còn chỗ
        pad = " " * max(1, len(m.group("head")) + len(old_val) - len(head) - len(new_val))
        comment = pad + comment.lstrip()
    lines[line_no] = f"{head}{new_val}{comment}{nl}"
    # write_rule kiểm tra lại bằng yaml.safe_load; sai thì dùng phương án dự phòng.
    return "".join(lines)


_WRITE_LOCK = threading.Lock()  # máy chủ API chạy một tiến trình; khóa tránh hai lần duyệt ghi chồng nhau


def _atomic_write(path: Path, text: str) -> None:
    """Ghi file tạm cùng thư mục rồi đổi tên: không bao giờ để lại rules.yaml ghi dở."""
    fd, tmp = tempfile.mkstemp(prefix=".rules-", suffix=".tmp", dir=str(Path(path).parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_rule(path: Path, key: str, value: Any) -> str:
    """Ghi giá trị vào rules.yaml (giữ chú thích, định dạng), tăng rules_version. Trả phiên bản mới."""
    with _WRITE_LOCK:
        return _write_rule_locked(Path(path), key, value)


def _write_rule_locked(path: Path, key: str, value: Any) -> str:
    yaml = YAML()
    yaml.preserve_quotes = True
    text = path.read_text(encoding="utf-8")
    data = yaml.load(text)
    parts = key.split(".")
    node = data
    for p in parts[:-1]:
        node = node[p]
    new_version = bump_minor(str(data["rules_version"]))
    edited = _edit_line(text, node.lc.key(parts[-1])[0], value)
    if edited is not None:
        edited = _edit_line(edited, data.lc.key("rules_version")[0], new_version)
    if edited is not None:
        check = safe_yaml.safe_load(edited)
        cur = check
        for p in parts:
            cur = cur[p]
        if cur == value and str(check["rules_version"]) == new_version:
            _atomic_write(path, edited)
            return new_version
    # Dự phòng: ghi lại toàn bộ bằng ruamel (có thể đổi cách xuống dòng)
    node[parts[-1]] = value
    data["rules_version"] = new_version
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    buf = io.StringIO()
    yaml.dump(data, buf)
    _atomic_write(path, buf.getvalue())
    return new_version


def propose(s: Session, key: str, new_value: Any, reason: str, *, role: str, user_id: int | None,
            rules: Rules | None = None) -> RuleChange:
    rules = rules or load_rules()
    meta = rules.get("rule_meta", {}).get(key)
    if meta is None:
        raise RuleChangeError(f"Khóa '{key}' không có trong danh mục cấu hình được phép sửa.")
    if is_locked(rules, key):
        raise RuleChangeError(f"Khóa '{key}' bị khóa an toàn – không sửa được qua giao diện.")
    if meta.get("admin_only"):
        require(role, "change_channel")
    else:
        require(role, "propose_rule")
    old = _get(rules.data, key)
    if not _same_shape(old, new_value):
        raise RuleChangeError(f"Giá trị mới không đúng dạng của '{key}' (hiện tại: {old!r}).")
    if not reason.strip():
        raise RuleChangeError("Cần ghi lý do thay đổi.")
    ch = RuleChange(key=key, old_value=old, new_value=new_value, reason=reason, proposed_by=user_id,
                    proposed_at=now(), status="pending")
    s.add(ch)
    s.flush()
    audit.log("propose_rule", "rule_change", ch.id, {"key": key, "old": old, "new": new_value, "reason": reason},
              user_id=user_id, session=s)
    # Khóa không cần bác sĩ duyệt (vận hành) hoặc kênh gửi tin do admin đổi → có hiệu lực ngay
    if not meta.get("requires_doctor"):
        _apply(s, ch, rules, user_id, auto=True)
    return ch


def _apply(s: Session, ch: RuleChange, rules: Rules, user_id: int | None, auto: bool = False) -> str:
    current = load_rules(rules.path)
    if is_locked(current, ch.key):
        raise RuleChangeError("Khóa đã bị khóa.")
    version = write_rule(rules.path, ch.key, ch.new_value)
    ch.status, ch.decided_by, ch.decided_at, ch.new_rules_version = "approved", user_id, now(), version
    audit.log("apply_rule" if auto else "approve_rule", "rule_change", ch.id,
              {"key": ch.key, "new": ch.new_value, "rules_version": version}, user_id=user_id, session=s)
    return version


def approve(s: Session, change_id: int, *, role: str, user_id: int | None, rules: Rules | None = None) -> str:
    if not can(role, "approve_rule"):
        raise PermissionDenied("Chỉ bác sĩ được duyệt thay đổi quy tắc.")
    ch = s.get(RuleChange, change_id)
    if ch is None or ch.status != "pending":
        raise RuleChangeError("Yêu cầu không tồn tại hoặc đã xử lý.")
    return _apply(s, ch, rules or load_rules(), user_id)


def reject(s: Session, change_id: int, *, role: str, user_id: int | None, note: str = "") -> None:
    if not can(role, "approve_rule"):
        raise PermissionDenied("Chỉ bác sĩ được từ chối thay đổi quy tắc.")
    ch = s.get(RuleChange, change_id)
    if ch is None or ch.status != "pending":
        raise RuleChangeError("Yêu cầu không tồn tại hoặc đã xử lý.")
    ch.status, ch.decided_by, ch.decided_at = "rejected", user_id, now()
    audit.log("reject_rule", "rule_change", ch.id, {"key": ch.key, "note": note}, user_id=user_id, session=s)
