"""Kiểm tra sâu MỌI luồng trên máy chủ thật, KHÔNG dùng khóa API (13/11/2026). Dữ liệu GIẢ, chạy trên BẢN SAO.

Cần: Playwright (pip) + Microsoft Edge. Các bước (Git Bash):
  W=/tmp/deepcheck; mkdir -p $W/data $W/shots; cp data/app.db config/rules.yaml $W/
  APP_DB_URL=sqlite:///$W/app.db APP_DATA_DIR=$W/data APP_RULES_FILE=$W/rules.yaml LLM_PROVIDER=mock     QUESTION_SELECTOR_PROVIDER=rules OPENAI_API_KEY= TEAMS_WEBHOOK_URL= SMS_TEST_API_URL= APP_JOBS=0     python -m uvicorn api.main:app --port 8000 &
  python scripts/deep_check.py $W
Kết quả: bảng OK/LỖI từng bước, ảnh chụp mọi màn ở $W/shots, results.json.
"""
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
W = Path(sys.argv[1])
BASE = "http://127.0.0.1:8000"
PW = {"nurse": "nurse123", "doctor": "doctor123", "admin": "admin123"}
results: list[tuple[str, str, bool, str]] = []


def check(area: str, what: str, ok: bool, detail: str = "") -> bool:
    results.append((area, what, bool(ok), detail))
    print(("  OK  " if ok else "  LỖI ") + f"[{area}] {what}" + (f" – {detail}" if detail and not ok else ""))
    return bool(ok)


def db(sql: str, *args):  # type: ignore[no-untyped-def]
    con = sqlite3.connect(W / "app.db")
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


for _ in range(60):
    try:
        urllib.request.urlopen(BASE + "/login", timeout=2)
        break
    except Exception:  # noqa: BLE001
        time.sleep(1)

with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge")
    ctx = {}

    def session(role: str):  # type: ignore[no-untyped-def]
        c = browser.new_context(viewport={"width": 1440, "height": 950})
        r = c.request.post(BASE + "/api/auth/login", data={"username": role, "password": PW[role]})
        check("Đăng nhập", f"{role} đăng nhập", r.status == 200, r.text()[:120])
        return c

    anon = browser.new_context()
    check("Phân quyền", "chưa đăng nhập → 401", anon.request.get(BASE + "/api/overview").status == 401)
    r = anon.request.post(BASE + "/api/auth/login", data={"username": "nurse", "password": "sai"})
    check("Phân quyền", "sai mật khẩu → từ chối", r.status in (401, 403))
    for role in PW:
        ctx[role] = session(role)
    N, D, A = (ctx[r].request for r in ("nurse", "doctor", "admin"))

    # ------------------------------------------------ phân quyền
    check("Phân quyền", "điều dưỡng không xem Chất lượng AI (403)", N.get(BASE + "/api/quality").status == 403)
    check("Phân quyền", "admin không vào Lịch gửi form (403)", A.get(BASE + "/api/form-schedule").status == 403)
    check("Phân quyền", "bác sĩ không tải file HIS (403)",
          D.post(BASE + "/api/appointments/upload", multipart={"d": "2026-11-13", "use_ai": "false"}).status == 403)

    # ------------------------------------------------ 1. Lịch hẹn
    samples = N.get(BASE + "/api/appointments").json()["samples"]
    name = sorted(n for n in samples if n.startswith("his_export_"))[-1]
    d = name.removeprefix("his_export_").removesuffix(".xlsx")
    up = N.post(BASE + "/api/appointments/upload", multipart={"d": d, "use_ai": "true", "sample": name})
    check("Lịch hẹn", f"tải file HIS mẫu {name} (AI giả lập)", up.status == 200 and up.json().get("ok"), up.text()[:200])
    bad = N.post(BASE + "/api/appointments/upload", multipart={"d": d, "use_ai": "false", "sample": "his_error_swapped_cols.xlsx"})
    check("Lịch hẹn", "file nhầm cột → dừng, báo E02", bad.ok and any(x["code"] == "E02" for x in bad.json().get("report", [])))
    data = N.get(BASE + "/api/appointments", params={"d": d}).json()
    check("Lịch hẹn", "có danh sách lịch hẹn, SĐT dạng che", data["items"] and all("•" in i["phone_masked"] for i in data["items"]))
    need = [i for i in data["items"] if i["status"] == "needs_review" or (i["arrival_time"] is None and i["method"] != "waiting")]
    if need:
        check("Lịch hẹn", f"còn {len(need)} ca cần xác nhận → khóa xuất (409)",
              N.post(BASE + "/api/appointments/export", params={"d": d}).status == 409)
    okc = 0
    for it in need:
        det = N.get(BASE + f"/api/appointments/plan/{it['plan_id']}", params={"d": d}).json()
        t = None if det["method"] == "waiting" else det["default_time"]
        okc += N.post(BASE + f"/api/appointments/plan/{it['plan_id']}/confirm", data={"d": d, "arrival_time": t}).status == 200
    check("Lịch hẹn", f"xác nhận {okc}/{len(need)} ca", okc == len(need))
    ex = N.post(BASE + "/api/appointments/export", params={"d": d})
    check("Lịch hẹn", "xuất 2 file WorldSMS", ex.status == 200, ex.text()[:200])
    files = N.get(BASE + "/api/appointments", params={"d": d}).json()["exports"]
    dl = N.get(BASE + f"/api/appointments/export/{files[0]}") if files else None
    check("Lịch hẹn", "tải file xuất (.xlsx)", dl is not None and dl.status == 200 and dl.body()[:2] == b"PK")
    check("Lịch hẹn", "đường dẫn lạ bị chặn", N.get(BASE + "/api/appointments/export/..%2F..%2F.env").status == 404)

    # ------------------------------------------------ 2. Hồ sơ + bộ câu hỏi cá nhân hóa
    def pid_of(code: str) -> int:
        return N.get(BASE + "/api/profile", params={"q": code}).json()["patient_id"]

    p55, p57 = pid_of("R0455"), pid_of("R0457")
    for p in (p55, p57):
        N.post(BASE + f"/api/profile/{p}/consent/grant", data={"note": "Kiểm tra sâu"})
    fp = N.get(BASE + f"/api/patients/{p55}/form-plan").json()
    sinh = next((i for i in fp["items"] if i["code"] == "P-SINHNON"), None)
    check("Bộ câu hỏi", "song thai → bộ sinh non TỰ áp dụng (căn cứ ICD-10 + nhóm nguy cơ)", sinh and sinh["confirmed"])
    check("Bộ câu hỏi", "câu mơ hồ C2 bị cổng chất lượng loại", sinh and "C2" in [r["code"] for r in sinh["rejected"]])
    check("Bộ câu hỏi", "xem trước form xếp khối, có lý do", fp["preview"] and all(q["reasons"] for q in fp["preview"]["questions"]))
    fp57 = N.get(BASE + f"/api/patients/{p57}/form-plan").json()
    check("Bộ câu hỏi", "thiếu máu → 'chờ ngân hàng câu hỏi', không đề xuất câu kém", fp57["pending_flags"] == ["ANEMIA"])
    check("Bộ câu hỏi", "mã lạ → 422", N.put(BASE + f"/api/patients/{p55}/form-plan", data={"codes": ["X"]}).status == 422)
    check("Bộ câu hỏi", "bác sĩ không xác nhận được (403)",
          D.put(BASE + f"/api/patients/{p55}/form-plan", data={"codes": ["P-SINHNON"]}).status == 403)

    # ------------------------------------------------ 3. Lịch gửi form
    ov = N.get(BASE + "/api/form-schedule")
    check("Lịch gửi form", "mở màn (đề xuất tự hiện, số SMS)", ov.status == 200 and "links" in ov.json()["counts"], ov.text()[:200])
    send = ov.json()["send"] if ov.ok else []
    codes = [s_["code"] for s_ in send]
    check("Lịch gửi form", "mỗi thai phụ tối đa 1 SMS/ngày", len(codes) == len(set(codes)), str(codes))
    exf = N.post(BASE + "/api/form-schedule/export")
    check("Lịch gửi form", "xuất SMS (hoặc 409 nếu hôm nay không có form)", exf.status in (200, 409), exf.text()[:200])
    if exf.status == 200:
        rows = db("select method, model, rules_version from question_selection")
        check("Lịch gửi form", "mỗi đường dẫn có bộ câu đã lưu, chọn theo QUY TẮC (không AI ngoài)",
              rows and all(m == "rules" and mo is None for m, mo, _ in rows), str(rows[:3]))
    check("Lịch gửi form", "gửi nhắc 48 giờ", N.post(BASE + "/api/form-schedule/reminders").status == 200)

    # ------------------------------------------------ 4. Form thai phụ → phân mức → cảnh báo
    tok = N.post(BASE + "/api/forms/demo-token", data={"code": "R0455", "form": "F6"}).json()["token"]
    form = N.get(BASE + f"/api/public/form/{tok}").json()
    sel = db("select method, model from question_selection where token = ?", tok)
    check("Form thai phụ", "đường dẫn có bộ câu, chọn theo quy tắc", sel == [("rules", None)], str(sel))
    keys = [q["key"] for q in form["questions"]]
    check("Form thai phụ", "có lựa chọn 'Không chắc', có khối câu", "Không chắc" in form["yesno"] and all(q["group"] for q in form["questions"]))
    ans = {k: "Không" for k in keys if k[0] == "C" or k in ("DYSPNEA", "CALF", "ITCH")}
    sub = anon.request.post(BASE + f"/api/public/form/{tok}",
                            data={"answers": ans, "free_text": "chiều nay hoa mắt, mặt hơi sưng"})
    check("Form thai phụ", "ca 'chiều nay hoa mắt, mặt hơi sưng' → ĐỎ (kiểm thử chấp nhận 4)",
          sub.ok and sub.json()["level"] == "red", sub.text()[:200])
    check("Form thai phụ", "đường dẫn chỉ dùng 1 lần (409)",
          anon.request.post(BASE + f"/api/public/form/{tok}", data={"answers": {}}).status == 409)
    tok2 = N.post(BASE + "/api/forms/demo-token", data={"code": "R0457", "form": "F5"}).json()["token"]
    k2 = [q["key"] for q in anon.request.get(BASE + f"/api/public/form/{tok2}").json()["questions"]]
    a2 = {k: "Không" for k in k2 if not k.startswith(("WEIGHT", "BP", "GLU"))}
    a2[k2[0]] = "Không chắc"
    s2 = anon.request.post(BASE + f"/api/public/form/{tok2}", data={"answers": a2, "free_text": ""})
    check("Form thai phụ", "'Không chắc' → VÀNG (không bao giờ xanh)", s2.ok and s2.json()["level"] == "yellow", s2.text()[:200])
    tok3 = N.post(BASE + "/api/forms/demo-token", data={"code": "R0457", "form": "F5"}).json()["token"]
    q3 = anon.request.get(BASE + f"/api/public/form/{tok3}").json()["questions"]
    check("Form thai phụ", "lần sau: câu 'Không chắc' lên đầu (Cần hỏi lại)",
          q3[0]["group"] == "Cần hỏi lại" and q3[0]["key"] == k2[0], str([(q["key"], q["group"]) for q in q3[:2]]))

    # ------------------------------------------------ 5. Cảnh báo (bác sĩ xử lý ca đỏ)
    alerts = D.get(BASE + "/api/alerts").json()
    items = alerts.get("items", alerts) if isinstance(alerts, dict) else alerts
    red = next((a for a in items if a.get("level") == "red" and a.get("code") == "R0455"), None)
    check("Cảnh báo", "ca đỏ vừa gửi có trong danh sách", red is not None)
    if red:
        det = D.get(BASE + f"/api/alerts/{red['id']}").json()
        check("Cảnh báo", "chi tiết cảnh báo có tóm tắt, không lộ SĐT", "phone" not in json.dumps(det).lower() or "•" in json.dumps(det, ensure_ascii=False))
        h = D.post(BASE + f"/api/alerts/{red['id']}/handle", data={"note": "Đã gọi, hẹn vào khám ngay", "final_level": "keep_red", "close": True})
        check("Cảnh báo", "ca đỏ: đóng mà KHÔNG chọn quyết định xử trí → từ chối (quy tắc C6)", h.status == 422, h.text()[:200])
        h = D.post(BASE + f"/api/alerts/{red['id']}/handle",
                   data={"note": "Đã gọi, hẹn vào khám ngay", "final_level": "keep_red", "decision": "visit_today"})
        check("Cảnh báo", "bác sĩ chọn 'Hẹn khám hôm nay' → đóng ca đỏ", h.status == 200 and h.json().get("status") == "closed",
              h.text()[:200])

    # ------------------------------------------------ 6. Cấu hình quy tắc
    cfg = N.get(BASE + "/api/config").json()
    v0 = yaml.safe_load((W / "rules.yaml").read_text(encoding="utf-8"))["rules_version"]
    rules_ = {r["key"]: r for g in cfg["groups"] for r in g["rules"]}
    key = next(k for k, r in rules_.items() if r["requires_doctor"] and r["editable"]
               and isinstance(r["value"], int) and not isinstance(r["value"], bool))
    pr = N.post(BASE + "/api/config/propose", data={"key": key, "value": str(rules_[key]["value"] + 1), "reason": "Kiểm tra sâu"})
    check("Cấu hình", f"điều dưỡng đề xuất đổi '{key}' (cần bác sĩ) → chờ duyệt",
          pr.status == 200 and pr.json().get("status") == "pending", pr.text()[:200])
    pend = N.get(BASE + "/api/config").json()["pending"]
    chg = next((p_["id"] for p_ in pend if p_["key"] == key), None)
    if chg:
        check("Cấu hình", "điều dưỡng không duyệt được (403)", N.post(BASE + f"/api/config/changes/{chg}/approve").status == 403)
        ap = D.post(BASE + f"/api/config/changes/{chg}/approve")
        v1 = yaml.safe_load((W / "rules.yaml").read_text(encoding="utf-8"))["rules_version"]
        check("Cấu hình", f"bác sĩ duyệt → rules_version {v0} → {v1}", ap.status == 200 and v1 != v0, ap.text()[:200])
    check("Cấu hình", "khóa an toàn không đề xuất được",
          N.post(BASE + "/api/config/propose", data={"key": "combine.ai_only_escalates", "value": "false", "reason": "x"}).status in (403, 422))

    # ------------------------------------------------ 7. Bỏ hẹn, chất lượng, tổng quan
    check("Bỏ hẹn", "màn dự đoán bỏ hẹn", N.get(BASE + "/api/noshow").status == 200)
    cl = N.get(BASE + "/api/noshow/call-list.xlsx", params={"d": d})
    check("Bỏ hẹn", "tải danh sách gọi (.xlsx)", cl.status == 200 and cl.body()[:2] == b"PK")
    check("Chất lượng AI", "bác sĩ xem màn chất lượng", D.get(BASE + "/api/quality").status == 200)
    check("Tổng quan", "màn tổng quan", N.get(BASE + "/api/overview").status == 200)

    # ------------------------------------------------ 8. Nhật ký (luật 7)
    acts = {a for (a,) in db("select distinct action from audit_log")}
    for a in ("login", "upload_his", "export_worldsms", "view_form_plan", "auto_apply_form_plan", "select_questions",
              "form_submitted", "handle_alert", "propose_rule", "approve_rule", "create_demo_token"):
        check("Nhật ký", f"có ghi '{a}'", any(x.startswith(a) for x in acts), ", ".join(sorted(acts))[:300])
    pii = db("select count(*) from audit_log where details like '%0900%' or details like '%Thị%'")
    check("Nhật ký", "nhật ký không chứa SĐT / tên", pii == [(0,)], str(pii))

    # ------------------------------------------------ 9. Giao diện: mở mọi màn theo vai trò, bắt lỗi
    routes = {
        "nurse": ["/", "/lich-hen", "/lich-gui-form", "/canh-bao", "/ho-so?q=R0455", "/du-doan-bo-hen", "/cau-hinh", "/form-demo"],
        "doctor": ["/", "/canh-bao", "/ho-so?q=R0455", "/chat-luong-ai", "/cau-hinh"],
        "admin": ["/", "/du-doan-bo-hen", "/chat-luong-ai", "/cau-hinh"],
    }
    for role, paths in routes.items():
        page = ctx[role].new_page()
        errs: list[str] = []
        page.on("pageerror", lambda e, errs=errs: errs.append(f"JS: {e}"))
        page.on("console", lambda m, errs=errs: errs.append(f"console: {m.text}") if m.type == "error" else None)
        page.on("response", lambda r, errs=errs: errs.append(f"HTTP {r.status} {r.url}")
                if r.status >= 400 and "/api/" in r.url else None)
        for path in paths:
            n0 = len(errs)
            page.goto(BASE + path)
            page.wait_for_load_state("networkidle")
            if path.startswith("/ho-so"):
                for tab in ("Lịch sử khám", "Nhật ký form", "Nhật ký cảnh báo", "Bộ câu hỏi", "Đồng thuận", "Sau sinh"):
                    page.get_by_role("tab", name=tab).click()
                    page.wait_for_timeout(400)
            page.screenshot(path=str(W / "shots" / f"{role}{path.replace('/', '_').replace('?', '_') or '_home'}.png"), full_page=True)
            new = errs[n0:]
            check("Giao diện", f"{role} mở {path} không lỗi", not new, "; ".join(new)[:300])
        page.close()

    # Form thai phụ trên điện thoại
    tok4 = N.post(BASE + "/api/forms/demo-token", data={"code": "R0455", "form": "F5"}).json()["token"]
    mp = browser.new_page(viewport={"width": 390, "height": 860})
    errs = []
    mp.on("pageerror", lambda e: errs.append(str(e)))
    mp.goto(BASE + f"/form?token={tok4}")
    mp.wait_for_load_state("networkidle")
    mp.screenshot(path=str(W / "shots" / "public_form_mobile.png"), full_page=True)
    check("Giao diện", "form thai phụ trên điện thoại hiển thị, không lỗi JS",
          not errs and mp.get_by_text("Không chắc").count() > 0, "; ".join(errs)[:200])
    browser.close()

bad = [r for r in results if not r[2]]
print(f"\nTỔNG: {len(results)} kiểm tra, {len(results) - len(bad)} đạt, {len(bad)} lỗi")
(W / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
