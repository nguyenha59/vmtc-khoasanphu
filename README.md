# Trợ lý lịch hẹn & theo dõi thai kỳ (MVP)

Ứng dụng hỗ trợ Phòng khám Sản phụ khoa:

- **Lịch hẹn:** đọc file xuất từ HIS, lọc khoa, gộp lịch cùng ngày, tính giờ đến, tạo file nhắc hẹn WorldSMS, đánh dấu ca cần xác nhận.
- **Theo dõi thai kỳ:** gửi form theo mốc tuần thai, đọc câu trả lời, phân mức cảnh báo (xanh / vàng / đỏ), theo dõi xu hướng chỉ số.
- **Dự đoán bỏ hẹn:** gợi ý ca nên gọi điện hoặc nhắn tin thêm.
- **Quản trị:** cấu hình quy tắc có bác sĩ duyệt, nhật ký thao tác, màn chất lượng mô hình.

> MVP chỉ chạy với **dữ liệu giả** sinh bởi `scripts/gen_synthetic.py`. Không đưa dữ liệu bệnh nhân thật vào kho mã.

## Nguyên tắc thiết kế

- Ngưỡng y khoa và quy tắc nằm trong `config/rules.yaml`, không viết cứng trong code; mỗi kết quả ghi kèm `rules_version`.
- Phân mức gồm 3 lớp (quy tắc → xu hướng → mô hình ngôn ngữ). Mô hình **chỉ được nâng mức**, không bao giờ hạ mức mà lớp quy tắc đã đặt.
- Văn bản gửi cho mô hình ngôn ngữ phải được che dữ liệu cá nhân trước (`core/ai/masking.py`); còn sót số điện thoại / CCCD / tên thì không gửi, chuyển người duyệt.
- Mô hình ngôn ngữ chạy cục bộ qua Ollama (`LLM_PROVIDER=ollama` hoặc `mock`); không gửi dữ liệu ra dịch vụ bên ngoài.
- Tên, số điện thoại, ghi chú gốc được mã hóa (Fernet, khóa `APP_SECRET_KEY`); giao diện chỉ hiện số điện thoại dạng che.
- Mọi thao tác xem / sửa / duyệt / xử lý cảnh báo đều ghi vào `audit_log`.

## Công nghệ

- **Backend:** Python 3.11, FastAPI, SQLAlchemy (SQLite), LightGBM / scikit-learn
- **Frontend:** React + TypeScript (Vite), thư mục `web/`
- **Mô hình ngôn ngữ:** Ollama (mặc định `qwen2.5:7b-instruct`)
- **Kiểm thử:** pytest, ruff, black

## Cấu trúc thư mục

| Thư mục | Nội dung |
|---|---|
| `api/` | FastAPI: ứng dụng nội bộ (`main.py`) và ứng dụng form công khai (`public_main.py`) |
| `core/` | Nghiệp vụ dạng hàm thuần: đọc file, quy tắc, AI, kênh gửi tin, bảo mật, nhật ký |
| `config/` | `rules.yaml` (quy tắc, ngưỡng), `his_columns.yaml` (ánh xạ cột HIS), `forms.yaml`, `users.example.yaml` (mẫu tài khoản), prompt |
| `scripts/` | Sinh dữ liệu giả, nạp dữ liệu demo, huấn luyện, đánh giá, kiểm thử chấp nhận |
| `tests/` | Kiểm thử tự động, bộ đáp án và bộ dữ liệu đánh giá chuẩn |
| `web/` | Giao diện web |

## Cài đặt và chạy thử (Windows, Python 3.11)

```powershell
# 1. Môi trường
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# 2. Khóa mã hóa + file .env + config/users.yaml (tạo từ bản mẫu nếu chưa có)
python scripts/make_key.py

# 3. Mô hình ngôn ngữ cục bộ (Ollama) – bỏ qua nếu chỉ chạy LLM_PROVIDER=mock
#    Cài Ollama từ https://ollama.com/download rồi:
ollama pull qwen2.5:7b-instruct
#    Trong .env đặt: LLM_PROVIDER=ollama
#    KHÔNG dùng mô hình đuôi "-cloud" (chạy trên máy chủ Ollama) – ứng dụng sẽ chặn.

# 4. Dữ liệu giả + nạp CSDL demo + huấn luyện mô hình bỏ hẹn
python scripts/gen_synthetic.py --days 90 --per-day 110 --seed 42
python scripts/seed_demo.py --reset
python scripts/train_noshow.py

# 5. Đánh giá mô hình trên bộ dữ liệu chuẩn tests/benchmark/v1 (hiện ở màn Chất lượng AI)
#    --bench quick mất khoảng 30–60 phút với Ollama. --provider mock chỉ kiểm tra đường ống.
python scripts/run_eval.py --dataset notes  --provider ollama --bench quick
python scripts/run_eval.py --dataset forms  --provider ollama --bench quick
python scripts/run_eval.py --dataset noshow

# 6. Kiểm thử chấp nhận (file lịch ngày mai, form mẫu)
python scripts/run_acceptance.py

# 7. Chạy ứng dụng
cd web && npm install && npm run build && cd ..
python -m uvicorn api.main:app --port 8000
#    → http://localhost:8000  (API docs: /api/docs – tắt khi APP_ENV=production)
#    Phát triển giao diện: uvicorn --reload ở cổng 8000, rồi `cd web && npm run dev` → http://localhost:5173
```

Tài khoản demo: `nurse / nurse123`, `doctor / doctor123`, `admin / admin123`.
Sai mật khẩu 5 lần trong 15 phút → khóa tạm 15 phút.

## Triển khai thật

Chạy hai tiến trình tách biệt:

```powershell
# Nội bộ (nhân viên) – chỉ trong mạng LAN, KHÔNG mở ra Internet
python -m uvicorn api.main:app --host <IP LAN> --port 8000
# Công khai (thai phụ điền form) – đặt sau reverse proxy HTTPS
python -m uvicorn api.public_main:app --host 127.0.0.1 --port 8001
```

Trong `.env`: `APP_ENV=production`, `APP_COOKIE_SECURE=1`, `APP_BASE_URL=https://<địa chỉ công khai>`.
Máy chủ từ chối khởi động nếu còn mật khẩu demo hoặc chưa bật HTTPS – **đổi mật khẩu trong `config/users.yaml` trước khi dùng**.

Trước khi dùng thật cần phòng khám xác nhận: tên cột file HIS và mẫu WorldSMS, ngưỡng cảnh báo và mốc form (bác sĩ duyệt), kênh Teams nhận cảnh báo, máy chạy mô hình ngôn ngữ.

## Kiểm thử

```powershell
pytest
ruff check .
```
