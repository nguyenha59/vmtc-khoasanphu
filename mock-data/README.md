# Dữ liệu kiểm thử (Mock Data & Schema)

Dự án: **MVP "Trợ lý lịch hẹn & theo dõi thai kỳ"** cho Phòng khám Sản phụ khoa.

> ⚠️ **Toàn bộ là dữ liệu GIẢ.** Dữ liệu sinh bằng `scripts/gen_synthetic.py` và `scripts/make_oh_demo.py`.
> Tên, số điện thoại, mã bệnh nhân và mã cộng tác viên đều là ngẫu nhiên. Thư mục này không chứa dữ liệu bệnh nhân thật.

## Nội dung

| Thư mục / file | Nội dung |
|---|---|
| [`SCHEMA.md`](SCHEMA.md) | **Schema đầy đủ**: file OH đầu vào, 26 bảng CSDL, JSON kết quả AI, danh mục tri thức khám |
| [`oh/`](oh) | Lịch hẹn **định dạng file xuất OH thật** (6 cột). `his_oh_demo_2026-11-18.xlsx` là file demo 20 lịch, trong đó 2 khách tự đặt chưa có HN |
| [`legacy-his/`](legacy-his) | Lịch dạng cũ (nhiều cột) + 3 file **cố ý lỗi** (thiếu cột, nhầm cột giờ, giờ sai) để kiểm tra bước validate |
| [`tables/`](tables) | Bệnh nhân (~450), lịch sử lịch hẹn + bỏ hẹn, khách đến, chỉ số tại nhà, câu trả lời form + mức cảnh báo đúng |
| [`golden/`](golden) | **Bộ đáp án kiểm thử** (dùng trong `pytest`): 40 ghi chú OH có đáp án loại khám, dịch vụ, giờ đến; giờ đến và file WorldSMS mong đợi; đáp án đọc ghi chú; đáp án chọn câu hỏi form |
| [`schema/`](schema) | `ai_note_output.schema.json` (JSON schema của AI), `his_columns.yaml` (ánh xạ cột), `visit_services.yaml` + `danh_muc_kham.xlsx` (danh mục tri thức có nguồn) |

## Sinh lại

```bash
python scripts/gen_synthetic.py --seed 42      # dữ liệu giả 60 ngày, ~450 khách
python scripts/make_oh_demo.py --date 2026-11-18
python scripts/export_mock_package.py           # đóng gói lại thư mục mock-data/
```

## Dùng để kiểm thử

```bash
pytest                                  # toàn bộ test (dùng tests/golden)
python scripts/eval_oh_notes.py         # đo độ chính xác đọc ghi chú OH (quy tắc + AI mock)
```
