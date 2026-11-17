"""Đọc phần tự viết của form – các bước tách riêng (docs/05 mục 3).

Luồng (core/ai/form_reader.py ghép lại):
  1. `lexicon`   – danh mục khái niệm + phân tích khẳng định trên câu gốc (luật, không AI).
  2. `votes`     – lọc phiếu AI (trích dẫn phải có thật), gộp đa số, độ nhất quán.
  3. `concerns`  – mối lo ngoài danh mục: chỉ giữ khi bám văn bản, không phủ định / người khác / câu bình thường.
  4. `reconcile` – BẢNG đối chiếu danh mục ↔ AI theo thứ tự, rồi tính mức từng phát hiện (giới hạn hạ / nâng).
  `text` – hàm nhỏ trên văn bản dùng chung.
Mọi hàm là hàm thuần (văn bản, cấu hình → kết quả); hành vi khóa bởi tests/test_form_reader_behavior.py.
"""
