"""Bản chụp trang nguồn của danh mục tri thức khám (knowledge/README.md mục 6).

Mỗi lần tải một nguồn công khai, lưu bản CHỮ của trang (không lưu HTML / ảnh) kèm thông tin truy cập và mã băm:

    knowledge/sources/snapshots/<MÃ>/<YYYY-MM-DD>.txt        chữ của trang (đã bỏ menu, script…)
    knowledge/sources/snapshots/<MÃ>/<YYYY-MM-DD>.meta.json  link, link thật, giờ truy cập, HTTP, tiêu đề, sha256

PDF: lưu file gốc `.pdf`; chưa có công cụ trích chữ → `text_ok = false`, không dùng làm căn cứ cho câu trích.
Câu trích (`facts`) trỏ tới bản chụp; khi nạp, hệ thống kiểm câu trích có NGUYÊN VĂN trong bản chụp (luật 2 README).

Chỉ tải link công khai, không gửi dữ liệu bệnh nhân. Hàm thuần trừ `fetch` / `save_snapshot` / `latest_snapshot`
/ `read_snapshot` (đọc ghi file, mạng).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = "sources/snapshots"  # tương đối với thư mục knowledge/
MAX_BYTES = 8 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; knowledge-source-snapshot)"

# Trạng thái kiểm tra lại nguồn (knowledge_check_sources.py)
CHECK_STATUS = {
    "UNCHANGED": "Không đổi",
    "CHANGED_OK": "Trang đổi, câu trích vẫn còn",
    "QUOTE_GONE": "Câu trích không còn trên trang",
    "DEAD": "Link không mở được",
    "NO_SNAPSHOT": "Chưa có bản chụp để so",
}
CHECK_NEEDS_REVIEW = ("QUOTE_GONE", "DEAD")

_ZW = re.compile("[​‌‍⁠﻿­]")
_SPACES = re.compile(r"[\s  -   　]+")
_DASHES = re.compile("[‐-―−]")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "«": '"', "»": '"'})


@dataclass
class Snapshot:
    code: str
    path: str  # tương đối với thư mục knowledge/, dạng a/b/c
    sha256: str
    accessed: str  # ISO, múi giờ Asia/Bangkok
    url: str
    final_url: str
    status: int
    title: str
    kind: str  # html | pdf
    text_ok: bool


# ---------------------------------------------------------------- hàm thuần
def normalize(text: str) -> str:
    """Chuẩn hóa để so câu trích: NFC, bỏ ký tự vô hình, gộp khoảng trắng, thống nhất gạch nối / ngoặc kép, chữ thường."""
    s = unicodedata.normalize("NFC", text)
    s = _ZW.sub("", s).translate(_QUOTES)
    s = _DASHES.sub("-", s)
    s = _SPACES.sub(" ", s)
    s = re.sub(r" ([,.:;!?)\]])", r"\1", s)  # thẻ HTML tách chữ khỏi dấu câu: "tuổi , vào" = "tuổi, vào"
    s = re.sub(r"([(\[]) ", r"\1", s)
    return s.strip().casefold()


def quote_in_text(quote: str, text: str) -> bool:
    """Câu trích có NGUYÊN VĂN trong văn bản (sau chuẩn hóa). Dấu chấm/phẩy cuối câu trích không bắt buộc khớp."""
    q = normalize(quote).rstrip(" .;,")
    return bool(q) and q in normalize(text)


def html_to_text(raw: str) -> str:
    """HTML → chữ: bỏ script/style/menu/chân trang, mỗi khối một dòng, bỏ ký tự vô hình."""
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?is)<(script|style|noscript|nav|footer|header|svg|form)\b.*?</\1>", " ", raw)
    txt = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", raw))
    txt = _ZW.sub("", unicodedata.normalize("NFC", txt))
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in txt.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip() + "\n"


def html_title(raw: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()[:300] if m else ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare(old_sha: str | None, new_text: str | None, quotes: list[str]) -> str:
    """So bản mới tải với bản chụp đã lưu → mã trạng thái trong CHECK_STATUS."""
    if new_text is None:
        return "DEAD"
    if old_sha is None:
        return "NO_SNAPSHOT"
    if sha256_bytes(new_text.encode("utf-8")) == old_sha:
        return "UNCHANGED"
    return "CHANGED_OK" if all(quote_in_text(q, new_text) for q in quotes) else "QUOTE_GONE"


# ---------------------------------------------------------------- mạng + file
def fetch(url: str, timeout: int = 30) -> tuple[int, str, str, bytes]:
    """Tải link công khai → (HTTP status, link thật, content-type, nội dung). Chỉ http/https, tối đa 8 MB."""
    import requests

    if not re.match(r"^https?://", url or ""):
        raise ValueError(f"Link không hợp lệ: {url!r}")
    with requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, stream=True) as r:
        if not re.match(r"^https?://", r.url):
            raise ValueError("Chuyển hướng tới link không phải http(s)")
        body = b""
        for chunk in r.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_BYTES:
                raise ValueError("Trang lớn hơn 8 MB")
        return r.status_code, r.url, r.headers.get("Content-Type", ""), body


def decode_html(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    for enc in [m.group(1)] if m else []:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            pass
    return body.decode("utf-8", errors="replace")


def _code_dir(know_dir: Path, code: str) -> Path:
    if not re.fullmatch(r"[A-Z0-9_]+", code):
        raise ValueError(f"Mã nguồn không hợp lệ cho tên thư mục: {code!r}")
    return know_dir / SNAPSHOT_DIR / code


def latest_snapshot(know_dir: Path, code: str) -> Snapshot | None:
    d = know_dir / SNAPSHOT_DIR / code
    if not re.fullmatch(r"[A-Z0-9_]+", code) or not d.is_dir():
        return None
    metas = sorted(d.glob("*.meta.json"))
    if not metas:
        return None
    best = max((json.loads(p.read_text(encoding="utf-8")) for p in metas), key=lambda m: m["accessed"])
    return Snapshot(**best)


def read_snapshot(know_dir: Path, rel: str) -> str | None:
    """Chữ của bản chụp (None nếu không có / ngoài thư mục bản chụp / là PDF chưa trích chữ)."""
    base = (know_dir / SNAPSHOT_DIR).resolve()
    p = (know_dir / rel).resolve()
    if base not in p.parents or p.suffix != ".txt" or not p.is_file():
        return None
    return p.read_text(encoding="utf-8")


def file_sha(know_dir: Path, rel: str) -> str | None:
    p = (know_dir / rel).resolve()
    if (know_dir / SNAPSHOT_DIR).resolve() not in p.parents or not p.is_file():
        return None
    return sha256_bytes(p.read_bytes())


def save_snapshot(know_dir: Path, code: str, url: str, final_url: str, status: int, content_type: str,
                  body: bytes, now: datetime) -> Snapshot:
    """Lưu bản chụp. Nội dung trùng bản mới nhất → trả bản cũ, không tạo thêm file."""
    d = _code_dir(know_dir, code)
    is_pdf = "pdf" in content_type.lower() or body[:5] == b"%PDF-"
    if is_pdf:
        data, ext, title, text_ok = body, ".pdf", "", False
    else:
        raw = decode_html(body, content_type)
        data, ext, title, text_ok = html_to_text(raw).encode("utf-8"), ".txt", html_title(raw), True
    sha = sha256_bytes(data)
    prev = latest_snapshot(know_dir, code)
    if prev and prev.sha256 == sha:
        return prev
    d.mkdir(parents=True, exist_ok=True)
    stem = now.strftime("%Y-%m-%d")
    if (d / f"{stem}{ext}").exists():  # đã có bản khác trong ngày → thêm giờ phút
        stem = now.strftime("%Y-%m-%d_%H%M%S")
    (d / f"{stem}{ext}").write_bytes(data)
    snap = Snapshot(code=code, path=f"{SNAPSHOT_DIR}/{code}/{stem}{ext}", sha256=sha,
                    accessed=now.isoformat(timespec="seconds"), url=url, final_url=final_url, status=status,
                    title=title, kind="pdf" if is_pdf else "html", text_ok=text_ok)
    (d / f"{stem}.meta.json").write_text(json.dumps(asdict(snap), ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    return snap


def snapshot_source(know_dir: Path, code: str, url: str, now: datetime) -> Snapshot:
    status, final_url, ctype, body = fetch(url)
    if status >= 400:
        raise ValueError(f"HTTP {status}")
    return save_snapshot(know_dir, code, url, final_url, status, ctype, body, now)


def as_meta(snap: Snapshot) -> dict[str, Any]:
    return asdict(snap)
