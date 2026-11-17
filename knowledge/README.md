# Danh mục tri thức khám – quy trình nạp tài liệu

Hệ thống đọc ghi chú lịch hẹn OH để suy ra **loại khám**, **dịch vụ** (siêu âm, xét nghiệm, CTG, khám gây mê…) và
**giờ khách nên đến**. Toàn bộ "kiến thức" cho việc này nằm trong **một sổ Excel** do khoa quản lý:
`knowledge/danh_muc_kham.xlsx`. Hệ thống không tự học từ Internet khi chạy. Nó chỉ dùng những gì đã được khoa duyệt
trong sổ này.

```
 Khoa sửa sổ Excel ──► Kiểm tra tự động ──► Người có thẩm quyền duyệt ──► Nạp (tăng phiên bản)
        ▲                (lỗi → từ chối)        (sheet Phê duyệt)          config/visit_services.yaml
        │                                                                   + CHANGELOG + audit_log
        └──────────── Xuất lại sổ từ phiên bản đang chạy ◄──────────────────────────┘
```

## 1. Các sheet trong sổ

| Sheet | Nội dung | Ai sửa |
|---|---|---|
| Hướng dẫn | Quy tắc điền | – |
| Phê duyệt | Người duyệt, chức danh, ngày, nội dung thay đổi. **Bắt buộc** điền trước khi nạp | Trưởng khoa / người được ủy quyền |
| Dịch vụ | Mã, tên, từ khóa, số phút dự phòng, **tối thiểu có nguồn**, giới hạn an toàn, nhịn ăn, nguồn | Điều dưỡng trưởng |
| Loại khám | Thứ tự ưu tiên, từ khóa, dịch vụ thường làm, nguồn | Bác sĩ |
| Viết tắt | Viết tắt trong ghi chú → mã dịch vụ / loại khám; trạng thái **Thông dụng / Đã xác nhận / Đang đoán** | Bác sĩ, điều dưỡng |
| Mốc tuần thai | Khoảng tuần → dịch vụ thường làm; "Cần xác nhận" nếu cần nhịn ăn | Bác sĩ |
| Đã làm | Cụm từ "đã làm…" để không tính lại dịch vụ | Điều dưỡng |
| Quy trình | Giờ hẹn là giờ gì, dịch vụ làm trước hay sau bác sĩ, thời gian tiếp đón | Khoa |
| Nguồn | Mã, cấp A/B/C/X, link, đã kiểm trang gốc chưa, ghi chú kiểm chứng | Người soạn |
| Trích dẫn | **Câu nguyên văn** từ trang nguồn + dịch vụ áp dụng; Đã duyệt / Chờ duyệt / Loại | Người soạn → người duyệt |
| Việc nội bộ | Dữ liệu nội bộ còn thiếu (số phút khoa chốt, giờ thật từ HIS, từ điển viết tắt…) | Khoa |

## 2. Quy tắc để một thông tin được hệ thống dùng

1. **Nguồn**: cấp A (Vinmec, bệnh viện lớn, Bộ Y tế) **và** "Đã kiểm trang gốc = Có" (đã mở trang, đối chiếu).
   Cấp B chỉ để tham khảo, cấp C chỉ để đối chiếu, cấp X không dùng.
2. **Trích dẫn**: chép **nguyên văn** từ trang gốc, trạng thái "Đã duyệt". Chỉ những câu này được đưa cho AI.
   AI phải ghi mã nguồn đã dùng; nguồn không có trong danh mục thì bị loại.
3. **Số phút tối thiểu** của dịch vụ phải có nguồn đã kiểm. AI ước lượng thấp hơn
   "tiếp đón + tổng tối thiểu các dịch vụ làm trước" thì lịch vào **Cần xác nhận**.
4. **Viết tắt "Đang đoán"**: hệ thống vẫn hiểu theo nghĩa đã ghi, nhưng lịch có viết tắt đó vào **Cần xác nhận**
   cho tới khi bác sĩ / điều dưỡng xác nhận (đổi thành "Đã xác nhận", ghi tên, ngày).
5. Số phút **dự phòng** (cột "Đến trước dự phòng") chỉ dùng khi AI không chạy được. Chưa có căn cứ.

## 3. Các bước

### Bước 1 – Xuất sổ từ phiên bản đang chạy
```
python scripts/knowledge_export.py
```
→ `knowledge/danh_muc_kham.xlsx` (luôn sửa trên bản mới xuất, không sửa bản cũ).

### Bước 2 – Thêm / kiểm nguồn
- Thêm dòng vào sheet **Nguồn** (link, cấp, tổ chức).
- Lấy câu gợi ý từ trang (máy chỉ **gợi ý**, không tự duyệt):
  ```
  python scripts/knowledge_fetch_quotes.py knowledge/danh_muc_kham.xlsx --codes VMC_SA_11_DIEU,TA_3_MOC
  ```
  Câu gợi ý được thêm vào sheet **Trích dẫn** với trạng thái "Chờ duyệt". Có cả câu không liên quan
  (câu hỏi độc giả, quảng cáo) → người duyệt phải chọn.
- Người duyệt mở trang gốc, đối chiếu từng câu: đổi thành **Đã duyệt** (điền "Dịch vụ áp dụng") hoặc **Loại**;
  đổi "Đã kiểm trang gốc" của nguồn thành **Có**, ghi ngày kiểm.

### Bước 3 – Sửa tri thức
Sửa các sheet Dịch vụ / Loại khám / Viết tắt / Mốc tuần thai / Quy trình. Mỗi dòng phải có mã nguồn.

### Bước 4 – Kiểm tra (không ghi gì)
```
python scripts/knowledge_import.py knowledge/danh_muc_kham.xlsx
```
In danh sách thay đổi, cảnh báo, lỗi. **Có lỗi thì không nạp được**, ví dụ: mã dịch vụ không tồn tại, trích dẫn từ
nguồn chưa kiểm, số phút tối thiểu không có nguồn, số phút dự phòng vượt giới hạn an toàn.

### Bước 5 – Duyệt và nạp
Điền sheet **Phê duyệt** (Người duyệt, Chức danh, Ngày duyệt, Nội dung thay đổi), rồi:
```
python scripts/knowledge_import.py knowledge/danh_muc_kham.xlsx --apply
```
Hệ thống: lưu bản cũ vào `knowledge/history/`, tăng phiên bản, ghi `config/visit_services.yaml`,
ghi `knowledge/CHANGELOG.md` và `audit_log` (`knowledge_import`). Lịch xử lý sau đó ghi kèm phiên bản danh mục.

### Bước 6 – Kiểm tra lại sau nạp
```
pytest tests/test_knowledge.py tests/test_visit_infer.py tests/test_oh_ingest.py
python scripts/eval_oh_notes.py
```

## 4. Quay lại phiên bản cũ
Chép `knowledge/history/visit_services_<phiên bản>.yaml` đè lên `config/visit_services.yaml`, xuất lại sổ, ghi lý do
vào sheet Phê duyệt, nạp lại như bước 5 (để có nhật ký và audit).

## 5. Việc nội bộ còn thiếu (xem sheet "Việc nội bộ")
Nguồn công khai **không quy định số phút đến trước**, chỉ cho thời lượng tối thiểu (siêu âm 15–30', CTG ≥ 30',
OGTT ~2 giờ). Để giờ đến sát thực tế cần: bảng số phút khoa chốt (NB_SO_PHUT), giờ thật từ HIS 1–2 tháng
(NB_HIS_TIMESTAMP), từ điển viết tắt được xác nhận (NB_VIET_TAT), bảng quyền lợi gói hiện hành (NB_GOI_HIEN_HANH),
bộ đáp án do điều dưỡng gán (NB_GOLD).
