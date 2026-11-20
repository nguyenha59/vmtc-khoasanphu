# Danh mục tri thức khám – quy trình nạp tài liệu

Hệ thống đọc ghi chú lịch hẹn OH để suy ra **loại khám** và **dịch vụ** (siêu âm, xét nghiệm, CTG, khám gây mê…).
Sau đó **code tự tính giờ khách nên đến** từ thời gian tối thiểu có nguồn của từng dịch vụ. **AI không đặt số phút.**
Toàn bộ "kiến thức" cho việc này nằm trong **một sổ Excel** do khoa quản lý: `knowledge/danh_muc_kham.xlsx`.
Khi chạy, hệ thống không lên Internet. Nó chỉ dùng những gì đã được duyệt trong sổ này, và mỗi câu trích làm căn cứ có
**bản chụp trang gốc** lưu trong `knowledge/sources/snapshots/`.

```
 Thêm link ──► Chụp trang (bản chữ + mã băm) ──► Chọn câu trích nguyên văn ──► Kiểm tra tự động ──► Duyệt ──► Nạp
                                                                             (câu phải có trong     (sheet      (tăng phiên bản,
                                                                              bản chụp, có nguồn)    Phê duyệt)   CHANGELOG, audit_log)
     ▲                                                                                                             │
     └────── Kiểm tra lại định kỳ: trang đổi / câu không còn / link chết → người duyệt xem lại ◄───────────────────┘
```

## 0. Thư mục `knowledge/`

| Đường dẫn | Nội dung | Hệ thống nạp? |
|---|---|---|
| `danh_muc_kham.xlsx` | Sổ tri thức khám (mục 1) | Có – qua `scripts/knowledge_import.py` → `config/visit_services.yaml` |
| `sources/snapshots/<MÃ>/<ngày>.txt` + `.meta.json` | Bản chụp chữ của trang nguồn + link, link thật, giờ truy cập, HTTP, tiêu đề, SHA-256 (mục 6). PDF lưu `.pdf` | Không – bằng chứng; câu trích trỏ tới đây và được kiểm khi nạp |
| `sources/kiem_tra/<ngày>.md` | Báo cáo kiểm tra lại nguồn (mục 6) | Không |
| `history/visit_services_<phiên bản>.yaml` | Bản danh mục trước mỗi lần nạp (để quay lại, mục 4) | Không |
| `CHANGELOG.md` | Nhật ký các lần nạp | Không |
| `sources/y-khoa.md` | Nguồn y khoa cho cảnh báo form (mã S1–S5, BYT1470, EPDS… dùng trong `config/rules.yaml` → `red_basis`) và nguồn cho danh mục khám | Không – tài liệu tra cứu; mã nguồn được chép vào cấu hình khi bác sĩ duyệt |
| `sources/tich-hop-du-lieu.md` | Nguồn chính thống: máy xét nghiệm, chuẩn dữ liệu, pháp lý, ICD-10, dữ liệu công khai | Không – tài liệu tra cứu |

## 1. Các sheet trong sổ

| Sheet | Nội dung | Ai sửa |
|---|---|---|
| Hướng dẫn | Quy tắc điền | – |
| Phê duyệt | Người duyệt, chức danh, ngày, nội dung thay đổi. **Bắt buộc** điền trước khi nạp | Trưởng khoa / người được ủy quyền |
| Dịch vụ | Mã, tên, từ khóa, số phút dự phòng, **tối thiểu có nguồn** + mã nguồn, giới hạn an toàn, nhịn ăn, nguồn | Điều dưỡng trưởng |
| Loại khám | Thứ tự ưu tiên, từ khóa, dịch vụ thường làm, nguồn | Bác sĩ |
| Viết tắt | Viết tắt trong ghi chú → mã dịch vụ / loại khám; trạng thái **Thông dụng / Đã xác nhận / Đang đoán** | Bác sĩ, điều dưỡng |
| Mốc tuần thai | Khoảng tuần → dịch vụ thường làm; "Cần xác nhận" nếu cần nhịn ăn | Bác sĩ |
| Đã làm | Cụm từ "đã làm…" để không tính lại dịch vụ | Điều dưỡng |
| Quy trình | Giờ hẹn là giờ gì, dịch vụ làm trước hay sau bác sĩ, thời gian tiếp đón | Khoa |
| Nguồn | Mã, cấp A/N/B/C/X, link, đã kiểm trang gốc chưa, ngày kiểm, ghi chú kiểm chứng; với cấp N: **Người quyết định, Tình trạng quyết định (Tạm / Đã chốt), Hạn xem lại**; **Kiểm tự động, Ngày kiểm tự động** (máy ghi, mục 6) | Người soạn |
| Trích dẫn | **Câu nguyên văn** từ trang nguồn, **Ghi chú** của người soạn (không viết vào câu), dịch vụ áp dụng, Đã duyệt / Chờ duyệt / Loại, ngày truy cập, **Bản chụp**, **SHA-256** | Người soạn → người duyệt |
| Việc nội bộ | Dữ liệu nội bộ còn thiếu (số phút khoa chốt, giờ thật từ HIS, từ điển viết tắt…) | Khoa |

## 2. Quy tắc để một thông tin được hệ thống dùng

1. **Cấp nguồn**:
   - **A**: Vinmec, bệnh viện lớn, Bộ Y tế. Phải "Đã kiểm trang gốc = Có", có link, đã chụp trang.
   - **N**: quyết định nội bộ của khoa / chủ dự án, không có link. Phải ghi Người quyết định, Ngày kiểm (ngày quyết
     định), Tình trạng (Tạm / Đã chốt), và Hạn xem lại nếu Tạm. Cột "Căn cứ" in rõ "Nội bộ – … (tạm)".
   - **B**: chỉ để tham khảo. **C**: chỉ để đối chiếu. **X**: không dùng.
2. **Trích dẫn**: chép **nguyên văn** từ trang gốc, mỗi câu một dòng (không gộp bằng " / ", không chèn chú thích vào câu,
   chú thích ghi ở cột "Ghi chú"). Phải có **Bản chụp**. Khi nạp, máy kiểm câu có nguyên văn trong bản chụp, sau khi bỏ
   khác biệt khoảng trắng, ký tự vô hình, gạch nối, chữ hoa/thường. Không khớp, hoặc bản chụp bị sửa (mã băm khác),
   thì **không nạp được**. Trích dẫn chỉ lấy từ nguồn công khai (A/B), không lấy từ cấp N.
3. **Số phút tối thiểu** của dịch vụ phải có nguồn **cấp A** hoặc **cấp N**. Với cấp A, số phút nên xuất hiện trong một
   câu trích đã duyệt của nguồn đó ("30 phút", "15–30 phút", "2 giờ" = 120'); không có thì cảnh báo. Với cấp N tạm thì
   mỗi lần nạp đều cảnh báo cho tới khi khoa chốt. **Code** cộng tiếp đón + tối thiểu của các dịch vụ làm trước bác sĩ
   để ra giờ đến. AI chỉ chọn dịch vụ, không đặt số phút.
4. **Viết tắt "Đang đoán"**: hệ thống vẫn hiểu theo nghĩa đã ghi, nhưng lịch có viết tắt đó vào **Cần xác nhận**
   cho tới khi bác sĩ / điều dưỡng xác nhận (đổi thành "Đã xác nhận", ghi tên, ngày).
5. Số phút **dự phòng** (cột "Đến trước dự phòng") chưa có căn cứ, chỉ là giới hạn dưới cũ, không dùng để tính giờ
   đến với file OH.

## 3. Các bước

### Bước 1 – Xuất sổ từ phiên bản đang chạy
```
python scripts/knowledge_export.py
```
→ `knowledge/danh_muc_kham.xlsx` (luôn sửa trên bản mới xuất, không sửa bản cũ).

### Bước 2 – Thêm nguồn, chụp trang, chọn câu trích
- Thêm dòng vào sheet **Nguồn** (link, cấp, tổ chức). Nạp sổ một lần (bước 4–5) để mã nguồn mới vào hệ thống, hoặc
  chụp bằng `--codes` sau khi nạp.
- Chụp trang:
  ```
  python scripts/knowledge_snapshot.py --codes VMC_SA_11_DIEU,TA_3_MOC
  ```
  Bản chụp trùng nội dung bản mới nhất thì không tạo thêm file. Link chết / bị chặn thì báo, không dừng cả lượt.
- Lấy câu gợi ý từ trang (máy chỉ **gợi ý**, không tự duyệt; câu gợi ý đã gắn sẵn bản chụp):
  ```
  python scripts/knowledge_fetch_quotes.py knowledge/danh_muc_kham.xlsx --codes VMC_SA_11_DIEU,TA_3_MOC
  ```
  Câu gợi ý vào sheet **Trích dẫn** với trạng thái "Chờ duyệt". Có cả câu không liên quan (câu hỏi độc giả, quảng cáo),
  nên người duyệt phải chọn.
- Câu tự chép tay (không qua script gợi ý): gắn bản chụp bằng
  ```
  python scripts/knowledge_snapshot.py --attach knowledge/danh_muc_kham.xlsx
  ```
  Câu không thấy nguyên văn sẽ được liệt kê. Sửa câu cho đúng nguyên văn, hoặc đổi thành "Loại".
- Người duyệt mở trang gốc, đối chiếu từng câu: đổi thành **Đã duyệt** (điền "Dịch vụ áp dụng") hoặc **Loại**;
  đổi "Đã kiểm trang gốc" của nguồn thành **Có**, ghi ngày kiểm.

### Bước 3 – Sửa tri thức
Sửa các sheet Dịch vụ / Loại khám / Viết tắt / Mốc tuần thai / Quy trình. Mỗi dòng phải có mã nguồn.

### Bước 4 – Kiểm tra (không ghi gì)
```
python scripts/knowledge_check_sources.py          # nên chạy trước: nguồn còn sống, câu trích còn trên trang
python scripts/knowledge_import.py knowledge/danh_muc_kham.xlsx
```
In danh sách thay đổi, cảnh báo, lỗi. **Có lỗi thì không nạp được**, ví dụ: mã dịch vụ không tồn tại, câu trích không có
nguyên văn trong bản chụp / chưa có bản chụp / bản chụp bị sửa, trích dẫn từ nguồn chưa kiểm hoặc từ cấp N, số phút tối
thiểu không có nguồn cấp A/N, quyết định nội bộ thiếu người quyết định / hạn xem lại, số phút dự phòng vượt giới hạn an toàn.

### Bước 5 – Duyệt và nạp
Điền sheet **Phê duyệt** (Người duyệt, Chức danh, Ngày duyệt, Nội dung thay đổi), rồi:
```
python scripts/knowledge_import.py knowledge/danh_muc_kham.xlsx --apply
```
Hệ thống: lưu bản cũ vào `knowledge/history/`, tăng phiên bản, ghi `config/visit_services.yaml`,
ghi `knowledge/CHANGELOG.md` và `audit_log` (`knowledge_import`). Lịch xử lý sau đó ghi kèm phiên bản danh mục.

### Bước 6 – Kiểm tra lại sau nạp
```
pytest tests/test_knowledge.py tests/test_knowledge_sources.py tests/test_visit_infer.py tests/test_oh_ingest.py
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

## 6. Bản chụp nguồn và kiểm tra lại định kỳ

**Vì sao cần.** Trang Vinmec / Tâm Anh có thể bị sửa, đổi link hoặc gỡ bài (lần chụp đầu 20/11/2026 đã gặp 2 trang
Vinmec trả HTTP 410). Bản chụp giữ bằng chứng câu trích đã có trên trang vào ngày duyệt, và lần sau dùng không cần mạng.

**Lưu gì.** Chỉ lưu **chữ** của trang (đã bỏ script, menu, chân trang), không lưu HTML / ảnh, để nhẹ và tránh lưu cả
bài của người khác. File `.meta.json` ghi link, link thật sau chuyển hướng, giờ truy cập (Asia/Bangkok), mã HTTP,
tiêu đề, SHA-256. PDF lưu file gốc nhưng chưa có công cụ trích chữ, nên **không làm căn cứ cho câu trích** (vd `BYT_1470`,
cần OCR). Bản chụp được commit vào git. Mỗi câu trích ghi `snapshot` + `sha256` + `accessed`.

**Kiểm tra lại** (hằng tháng và trước mỗi lần nạp):
```
python scripts/knowledge_check_sources.py --write knowledge/danh_muc_kham.xlsx
```
| Kết quả | Ý nghĩa | Việc cần làm |
|---|---|---|
| Không đổi | Trang giống hệt bản chụp | – |
| Trang đổi, câu trích vẫn còn | Đã lưu thêm bản chụp mới; câu trích vẫn trỏ bản lúc duyệt | Không bắt buộc |
| Câu trích không còn trên trang | Trang đã sửa nội dung | Mở trang gốc: còn đúng thì chọn câu mới, không thì đổi câu thành "Loại" và xem lại số phút dựa trên câu đó |
| Link không mở được | Gỡ bài, đổi link, chặn truy cập | Tìm link mới hoặc nguồn thay thế; bản chụp cũ vẫn là bằng chứng ngày duyệt |

Máy **không tự xóa** nguồn hay câu trích. `--write` ghi kết quả vào cột "Kiểm tự động" của sheet Nguồn, và khi nạp lại
các nguồn "Câu trích không còn" / "Link không mở được" được cảnh báo. Báo cáo lưu ở `knowledge/sources/kiem_tra/<ngày>.md`.
Mã thoát 1 nếu có nguồn cần xem lại, nên có thể đặt lịch chạy tự động.

**Quyết định nội bộ cấp N** (vd `NGUOI_DUNG_1811`: siêu âm 2D / đầu dò / Doppler 20'). Tình trạng "Tạm" thì mỗi lần
nạp đều cảnh báo kèm hạn xem lại. Khi khoa xác nhận, đổi Tình trạng thành "Đã chốt", ghi người quyết định là người của
khoa, rồi nạp lại.
