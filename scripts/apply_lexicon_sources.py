"""Thêm cách nói (có nguồn – knowledge/sources/y-khoa.md mục 4) vào cuối synonyms của khái niệm ĐÃ CÓ; tăng rules_version.

Chỉ thêm cách nói (chỉ có thể nâng mức). Không thêm khái niệm, không đổi mức – các việc đó chờ bác sĩ duyệt (mục 5).
Giữ nguyên chú thích, định dạng và kiểu xuống dòng của rules.yaml. Chạy lại lần hai không thêm trùng.

    python scripts/apply_lexicon_sources.py [--rules đường_dẫn] [--version 0.5.0]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.textutil import deaccent  # noqa: E402

ADDITIONS: dict[str, list[str]] = {
    "BLEEDING": ["máu cục", "ra cục máu", "rỉ máu", "thấm ướt băng", "ướt đẫm băng"],
    "REDUCED_FM": ["con không đạp", "bé không đạp", "thai không máy", "đạp yếu"],
    "VISUAL": ["nhìn nhòe", "mắt nhòe", "lóa mắt", "nhìn đôi", "nhìn không rõ"],
    "EPIGASTRIC": ["đau dưới sườn", "đau hạ sườn phải", "đau dưới xương sườn"],
    "FLUID": ["rỉ nước", "vỡ nước ối", "nước chảy ra âm đạo"],
    "DYSPNEA": ["thở nhanh", "thở dốc", "thở gấp"],
    "CALF": ["chân sưng đỏ", "sưng đỏ một bên chân"],
    "FACE_EDEMA": ["sưng mí mắt", "mí mắt sưng"],
    "HYPEREMESIS": ["ói nhiều", "nôn liên tục", "nôn ói nhiều"],
}


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def patch(text: str, version: str) -> tuple[str, dict[str, list[str]]]:
    data = yaml.safe_load(text)
    concepts = data["symptom_concepts"]
    added: dict[str, list[str]] = {}
    for code, words in ADDITIONS.items():
        have = set(concepts[code]["synonyms"])
        new = []
        for w in words:
            for v in (w, deaccent(w)):
                if v not in have and v not in new:
                    new.append(v)
        if not new:
            continue
        # dòng khai báo khái niệm → dòng chứa 'synonyms: [' của nó (khái niệm viết trên 1–2 dòng)
        m = re.search(rf"^  {re.escape(code)}:.*?synonyms: \[(?P<body>[^\]]*)\]", text, flags=re.M | re.S)
        if m is None:
            raise SystemExit(f"Không tìm thấy synonyms của {code}")
        end = m.end("body")
        text = text[:end] + ", " + ", ".join(_q(x) for x in new) + text[end:]
        added[code] = new
    old_v = str(data["rules_version"])
    if added and old_v != version:
        text = re.sub(r'^(rules_version:\s*)"?[^"\s#]+"?', lambda mm: f'{mm.group(1)}"{version}"', text, count=1, flags=re.M)
    check = yaml.safe_load(text)
    for code, new in added.items():
        assert set(new) <= set(check["symptom_concepts"][code]["synonyms"]), code
    return text, added


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=str(ROOT / "config" / "rules.yaml"))
    ap.add_argument("--version", default="0.5.0")
    a = ap.parse_args()
    p = Path(a.rules)
    raw = p.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    out, added = patch(text, a.version)
    if not added:
        print("Không có gì mới để thêm.")
        return
    if crlf:
        out = out.replace("\n", "\r\n")
    p.write_bytes(out.encode("utf-8"))
    for code, words in added.items():
        print(f"{code}: +{len(words)}")
    print(f"rules_version → {yaml.safe_load(out)['rules_version']}")


if __name__ == "__main__":
    main()
