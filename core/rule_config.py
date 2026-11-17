"""Thay đổi quy tắc (docs/04 F): đề xuất → bác sĩ duyệt → ghi rules.yaml, tăng rules_version."""

from __future__ import annotations

import io
import json
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml as safe_yaml
from ruamel.yaml import YAML
from sqlalchemy import event, select, update
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


_HHMM = re.compile(r"^\d{1,2}:\d{2}$")


def _invalid_value(key: str, value: Any, old: Any = None) -> str | None:
    """Lỗi giá trị (ngoài kiểu): số phải hữu hạn (không NaN / ∞), không đổi dấu so với giá trị hiện tại (thời hạn
    3 ngày → 0 / âm làm mọi đường dẫn hết hạn ngay), giờ "HH:MM" phải có thật. None = hợp lệ."""
    if isinstance(value, float) and not math.isfinite(value):
        return "số không hợp lệ (NaN / vô hạn)"
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and isinstance(old, (int, float)) and not isinstance(old, bool)):
        if old > 0 and value <= 0:
            return f"phải lớn hơn 0 (hiện tại {old})"
        if old >= 0 and value < 0:
            return f"không được âm (hiện tại {old})"
    if isinstance(value, str) and _HHMM.match(value.strip()):
        hh, mm = (int(x) for x in value.strip().split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return f"giờ '{value}' không có thật"
    if key.endswith("_times") and isinstance(value, list) and not all(
            isinstance(v, str) and _HHMM.match(v.strip()) for v in value):
        return "danh sách giờ phải dạng HH:MM"
    if isinstance(value, dict):
        olds = old if isinstance(old, dict) else {}
        return next((e for k, v in value.items() if (e := _invalid_value(key, v, olds.get(k)))), None)
    if isinstance(value, list):
        return next((e for v in value if (e := _invalid_value(key, v))), None)
    return None


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
    if old_val.strip().startswith(("|", ">")) or not old_val.strip():
        return None  # khối nhiều dòng / giá trị nằm ở các dòng thụt lề bên dưới (dict, list nhiều dòng)
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
        try:
            check = safe_yaml.safe_load(edited)
            cur = check
            for p in parts:
                cur = cur[p]
            ok = cur == value and str(check["rules_version"]) == new_version
        except (safe_yaml.YAMLError, KeyError, TypeError):
            ok = False  # sửa một dòng làm hỏng YAML → không ghi, dùng phương án dự phòng bên dưới
        if ok:
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
    if (bad := _invalid_value(key, new_value, old)) is not None:
        raise RuleChangeError(f"Giá trị mới của '{key}' không hợp lệ: {bad}.")
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


def _restore_if_rolled_back(s: Session, path: Path, before: str, written: str) -> None:
    """rules.yaml được ghi TRONG giao dịch CSDL (bản ghi thay đổi + nhật ký). Giao dịch bị hủy → khôi phục file,
    để cấu hình đang chạy luôn khớp với lịch sử thay đổi đã lưu. Chỉ khôi phục nếu file vẫn là nội dung mình ghi."""
    s.info.setdefault("rules_restore", []).append((path, before, written))
    if s.info.get("rules_restore_hooked"):
        return
    s.info["rules_restore_hooked"] = True

    def _rollback(session: Session) -> None:
        for p, old_text, new_text in reversed(session.info.pop("rules_restore", [])):
            with _WRITE_LOCK:
                if p.exists() and p.read_text(encoding="utf-8") == new_text:
                    _atomic_write(p, old_text)

    def _commit(session: Session) -> None:
        session.info.pop("rules_restore", None)

    event.listen(s, "after_rollback", _rollback)
    event.listen(s, "after_commit", _commit)


class RuleConflict(RuleChangeError):
    """Yêu cầu dựa trên giá trị / trạng thái đã cũ hoặc bị chính sách chặn (HTTP 409)."""


def _apply(s: Session, ch: RuleChange, rules: Rules, user_id: int | None, auto: bool = False) -> str:
    current = load_rules(rules.path)
    if is_locked(current, ch.key):
        raise RuleChangeError("Khóa đã bị khóa.")
    now_value = _get(current.data, ch.key)
    if now_value != ch.old_value:  # đề xuất lập trên giá trị cũ – một thay đổi khác đã được áp dụng sau đó
        raise RuleConflict(f"Giá trị hiện hành của '{ch.key}' là {now_value!r}, khác giá trị lúc đề xuất "
                           f"({ch.old_value!r}). Từ chối yêu cầu này và đề xuất lại trên giá trị mới.")
    if (bad := _invalid_value(ch.key, ch.new_value, _get(current.data, ch.key))) is not None:  # yêu cầu cũ
        raise RuleChangeError(f"Giá trị của '{ch.key}' không hợp lệ: {bad} – từ chối yêu cầu này.")
    before = Path(rules.path).read_text(encoding="utf-8")
    version = write_rule(rules.path, ch.key, ch.new_value)
    _restore_if_rolled_back(s, Path(rules.path), before, Path(rules.path).read_text(encoding="utf-8"))
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
    from core.quality import blocking_gate

    failed, unmeasured = blocking_gate(s)
    if failed:  # chỉ tiêu bắt buộc đo được mà chưa đạt → không kích hoạt phiên bản quy tắc mới
        raise RuleConflict("Chỉ tiêu chất lượng bắt buộc chưa đạt – chưa thể áp dụng thay đổi quy tắc: "
                           + "; ".join(failed) + ". Xem màn Chất lượng.")
    _claim(s, change_id)
    version = _apply(s, ch, rules or load_rules(), user_id)
    if unmeasured:
        audit.log("approve_rule_unmeasured_quality", "rule_change", ch.id, {"unmeasured": unmeasured},
                  user_id=user_id, session=s)
    return version


def _claim(s: Session, change_id: int) -> None:
    """Giành yêu cầu đang chờ: hai bác sĩ duyệt / từ chối cùng lúc chỉ một người xử lý (không ghi rules.yaml 2 lần)."""
    won = s.execute(update(RuleChange).where(RuleChange.id == change_id, RuleChange.status == "pending")
                    .values(status="processing").execution_options(synchronize_session=False)).rowcount
    if not won:
        raise RuleChangeError("Yêu cầu vừa được người khác xử lý – tải lại trang.")


def reject(s: Session, change_id: int, *, role: str, user_id: int | None, note: str = "") -> None:
    if not can(role, "approve_rule"):
        raise PermissionDenied("Chỉ bác sĩ được từ chối thay đổi quy tắc.")
    ch = s.get(RuleChange, change_id)
    if ch is None or ch.status != "pending":
        raise RuleChangeError("Yêu cầu không tồn tại hoặc đã xử lý.")
    _claim(s, change_id)
    ch.status, ch.decided_by, ch.decided_at = "rejected", user_id, now()
    audit.log("reject_rule", "rule_change", ch.id, {"key": ch.key, "note": note}, user_id=user_id, session=s)
