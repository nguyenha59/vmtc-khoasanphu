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
- Mô hình ngôn ngữ mặc định chạy cục bộ qua Ollama 7B, không cần API key. Chế độ `hybrid` ưu tiên OpenAI khi được phép và tự chuyển sang Ollama nếu API lỗi hoặc hết ngân sách. API ngoài **chỉ với dữ liệu giả**, cần `SYNTHETIC_DATA_ONLY=1`; hybrid trong production chỉ chạy Ollama.
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
#    .env mới mặc định: LLM_PROVIDER=ollama, OLLAMA_MODEL=qwen2.5:7b-instruct
#    KHÔNG dùng mô hình đuôi "-cloud" (chạy trên máy chủ Ollama) – ứng dụng sẽ chặn.

# 4. Dữ liệu giả + nạp CSDL demo + huấn luyện mô hình bỏ hẹn
python scripts/gen_synthetic.py --seed 42          # dữ liệu giả gọn: ~450 khách, 60 ngày
#    (ghi vào data/synthetic/, không sửa tests/golden – bộ cố định cho pytest; git pull không bị chặn)
python scripts/seed_demo.py --reset
python scripts/train_noshow.py   # chỉ để minh họa; mô hình thật phải huấn luyện lại khi có dữ liệu lịch hẹn thật

# 5. Đánh giá mô hình trên bộ dữ liệu chuẩn (hiện ở màn Chất lượng AI)
#    --bench quick mất khoảng 30–60 phút với Ollama. --provider mock chỉ kiểm tra đường ống.
python scripts/run_eval.py --dataset notes  --provider ollama --bench quick
python scripts/run_eval.py --dataset forms  --provider ollama --bench quick
python scripts/run_eval.py --dataset noshow

# Kiểm tra mô tả khách: bộ ca giả có kỳ vọng xanh/vàng/đỏ; xuất JSON + HTML, không ghi CSDL
python scripts/eval_customer_descriptions.py
python scripts/eval_customer_descriptions.py --provider ollama --model qwen2.5:7b-instruct --out reports/customer-descriptions/ollama-7b.json
# OpenAI trực tiếp: dùng key riêng trong .env, chỉ dữ liệu giả, không dự phòng Ollama
python scripts/eval_customer_descriptions.py --provider openai --out reports/customer-descriptions/openai.json

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

## Kết hợp OpenAI và Ollama

Người tải mã từ GitHub dùng Ollama `qwen2.5:7b-instruct` theo `.env.example` và `config/rules.yaml`. Cần cài Ollama và chạy lệnh `ollama pull` ở trên; kho mã không chứa trọng số mô hình hay API key. Nếu Ollama chưa sẵn sàng, ứng dụng tiếp tục bằng quy tắc và báo AI chưa dùng được.

Để thử cả hai nhà cung cấp **trên dữ liệu giả**, sửa `.env` riêng trên máy chủ:

```dotenv
LLM_PROVIDER=hybrid
OPENAI_API_KEY=<key riêng của bạn>
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BUDGET_USD=1.0
SYNTHETIC_DATA_ONLY=1
OLLAMA_MODEL=qwen2.5:7b-instruct
QUESTION_SELECTOR_PROVIDER=inherit
```

Khởi động lại backend sau khi đổi `.env`. `inherit` cho phần chọn câu hỏi dùng cùng chế độ với AI chính. Có thể ghi đè `OLLAMA_MODEL` theo máy riêng mà không sửa cấu hình công khai.

Hybrid không có key hoặc chưa được phép gửi ra ngoài sẽ chạy Ollama. Nếu một lần gọi OpenAI thất bại (kể cả lỗi xác thực, quota, JSON hoặc ngân sách), client dùng Ollama cho các lần gọi còn lại; yêu cầu mới có thể thử lại OpenAI. Nếu cả hai không dùng được, luồng nghiệp vụ trở về quy tắc. Ghi chú nhạy cảm luôn đi thẳng Ollama; kiểm tra che dữ liệu và các quy tắc cảnh báo vẫn áp dụng. Nhật ký AI lưu mô hình thực tế cho từng kết quả để kiểm tra khi chuyển nhà cung cấp.

`LLM_PROVIDER=openai` vẫn có thể dùng để thử riêng OpenAI và bị chặn trong production. `.env` đã được loại khỏi Git; giữ key ở backend theo [hướng dẫn OpenAI](https://developers.openai.com/api/docs/quickstart). Mô hình local dùng tag [Qwen2.5 7B Instruct](https://ollama.com/library/qwen2.5:7b-instruct).

## Triển khai thật

Chạy hai tiến trình tách biệt:

```powershell
# Nội bộ (nhân viên) – chỉ trong mạng LAN, KHÔNG mở ra Internet
python -m uvicorn api.main:app --host <IP LAN> --port 8000
# Công khai (thai phụ điền form) – đặt sau reverse proxy HTTPS
python -m uvicorn api.public_main:app --host 127.0.0.1 --port 8001 --proxy-headers --forwarded-allow-ips 127.0.0.1
# --proxy-headers: lấy IP thật của người dùng từ reverse proxy (để giới hạn tần suất theo từng người, không theo proxy)
```

Trong `.env`: `APP_ENV=production`, `APP_COOKIE_SECURE=1`, `APP_BASE_URL=https://<địa chỉ công khai>`.
Máy chủ từ chối khởi động nếu còn mật khẩu demo hoặc chưa bật HTTPS – **đổi mật khẩu trước khi dùng**: `python scripts/set_password.py <tên đăng nhập>` (gõ ẩn, ≥ 12 ký tự, bcrypt cost 12, thu hồi phiên cũ); tạo tài khoản mới thêm `--name "…" --role nurse|doctor|admin`.

Trước khi dùng thật cần phòng khám xác nhận: tên cột file HIS và mẫu WorldSMS, ngưỡng cảnh báo và mốc form (bác sĩ duyệt), kênh Teams nhận cảnh báo, máy chạy mô hình ngôn ngữ.

## Sao lưu

```powershell
python scripts/backup_db.py          # → data/backups/app_<thời điểm>.db, giữ 14 bản mới nhất
```
Dùng script này thay cho chép tay `data/app.db` (CSDL chạy chế độ WAL – dữ liệu mới có thể còn trong `app.db-wal`).
Bản sao lưu vẫn mã hóa, cần `APP_SECRET_KEY` để đọc – cất khóa ở nơi khác bản sao lưu.

## Kiểm thử

```powershell
pytest
ruff check .
```
