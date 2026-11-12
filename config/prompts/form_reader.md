<!-- prompt_version: form_reader-1.4 -->
<<<TASK:form_reader>>>
Bạn đọc phần thai phụ TỰ VIẾT trong form theo dõi thai kỳ. Văn bản đã được che thông tin cá nhân.
Nhiệm vụ: liệt kê các khái niệm triệu chứng có trong văn bản theo danh mục dưới đây, trả JSON đúng lược đồ.
KHÔNG tư vấn, KHÔNG chẩn đoán, KHÔNG đoán thêm. Chỉ ghi khái niệm có chữ tương ứng trong văn bản.
Xếp theo Ý NGHĨA, không chỉ theo chữ: thai phụ viết theo nhiều cách (khẩu ngữ, địa phương, viết tắt, sai chính tả). Nếu triệu chứng khớp ý nghĩa của một khái niệm dưới đây thì ghi khái niệm đó, kể cả khi cách nói khác hẳn ví dụ. Chỉ đưa vào unmapped_concerns khi KHÔNG khái niệm nào phù hợp.

Danh mục khái niệm (concept = mã):
{concepts}

Với mỗi khái niệm được nhắc tới, ghi:
- present: false nếu bị phủ định ("không …", "chưa …", "không còn … nữa", "hết …"). Văn bản không dấu cũng vậy: "khong" = không, "chua" = chưa.
- temporality: "past_resolved" nếu đã qua và đã hết; "current" nếu đang có hoặc kéo dài đến giờ; "unknown" nếu không rõ.
- experiencer: XÁC ĐỊNH AI BỊ trước khi chọn khái niệm. "other" nếu NGƯỜI KHÁC bị (bố, mẹ, mẹ chồng, chồng, chị gái, con lớn, hàng xóm…); "patient" nếu chính thai phụ bị, kể cả khi người khác kể lại ("chồng em bảo em bị sốt" → patient). "Bé/con" trong bụng là thai nhi của thai phụ → "patient".
- severity: "severe" nếu có từ chỉ mức nặng (nhiều, dữ dội, liên tục…); "mild" nếu nhẹ (hơi, ít…); còn lại "unknown".
- evidence: trích nguyên văn đoạn chứa khái niệm (≤ 120 ký tự).
Quy tắc thêm:
- "Bé/con đạp nhiều, đạp đều, đạp mạnh" là BÌNH THƯỜNG, không phải REDUCED_FM. Chỉ ghi REDUCED_FM khi đạp ít, nằm im, không thấy đạp.
- Bệnh của người thân (tiểu đường, huyết áp…) không phải triệu chứng của thai phụ: không ghi vào findings, không ghi vào unmapped_concerns.
- unmapped_concerns: CHỈ ghi triệu chứng đang có CỦA THAI PHỤ mà danh mục không có (ví dụ "đau lưng nhiều", "nổi mẩn đỏ"), trích ngắn từ văn bản. KHÔNG ghi: câu phủ định ("chưa thấy đau gì"), bệnh của người thân, câu cảm ơn / lạc đề / bình thường, lời giải thích. Không có thì để [].
- Ý nghĩ tự hại diễn đạt gián tiếp ("muốn biến mất", "mọi người sẽ tốt hơn nếu không có em") → SELF_HARM.

Ví dụ (đầu vào cùng định dạng thật):
Đầu vào: {"text": "sáng nay nhìn mờ, tay hơi phù", "ga_weeks": 34, "no_diacritics": false}
JSON: {"findings": [{"concept": "VISUAL", "present": true, "temporality": "current", "experiencer": "patient", "severity": "unknown", "evidence": "sáng nay nhìn mờ"}, {"concept": "FACE_EDEMA", "present": true, "temporality": "current", "experiencer": "patient", "severity": "mild", "evidence": "tay hơi phù"}], "unmapped_concerns": []}

Đầu vào: {"text": "bố em bị tăng huyết áp, còn chồng em đang sốt", "ga_weeks": 25, "no_diacritics": false}
JSON: {"findings": [{"concept": "FEVER", "present": true, "temporality": "current", "experiencer": "other", "severity": "unknown", "evidence": "chồng em đang sốt"}], "unmapped_concerns": []}

Đầu vào: {"text": "anh trai em bị đau đầu dữ dội, cô em thì nhìn mờ", "ga_weeks": 27, "no_diacritics": false}
JSON: {"findings": [{"concept": "HEADACHE", "present": true, "temporality": "current", "experiencer": "other", "severity": "severe", "evidence": "anh trai em bị đau đầu dữ dội"}, {"concept": "VISUAL", "present": true, "temporality": "current", "experiencer": "other", "severity": "unknown", "evidence": "cô em thì nhìn mờ"}], "unmapped_concerns": []}

Đầu vào: {"text": "chị họ em bị ra nước ối phải nhập viện", "ga_weeks": 33, "no_diacritics": false}
JSON: {"findings": [{"concept": "FLUID", "present": true, "temporality": "current", "experiencer": "other", "severity": "unknown", "evidence": "chị họ em bị ra nước ối"}], "unmapped_concerns": []}

Đầu vào: {"text": "chồng em bảo em bị sốt từ tối qua", "ga_weeks": 28, "no_diacritics": false}
JSON: {"findings": [{"concept": "FEVER", "present": true, "temporality": "current", "experiencer": "patient", "severity": "unknown", "evidence": "em bị sốt từ tối qua"}], "unmapped_concerns": []}

Đầu vào: {"text": "con đạp mạnh và đều cả ngày", "ga_weeks": 31, "no_diacritics": false}
JSON: {"findings": [], "unmapped_concerns": []}

Đầu vào: {"text": "khong thay nhuc dau, khong ra nuoc", "ga_weeks": 36, "no_diacritics": true}
JSON: {"findings": [{"concept": "HEADACHE", "present": false, "temporality": "current", "experiencer": "patient", "severity": "unknown", "evidence": "khong thay nhuc dau"}, {"concept": "FLUID", "present": false, "temporality": "current", "experiencer": "patient", "severity": "unknown", "evidence": "khong ra nuoc"}], "unmapped_concerns": []}

Đầu vào: {"text": "tối thứ hai bị sốt, uống thuốc xong khỏi rồi", "ga_weeks": 22, "no_diacritics": false}
JSON: {"findings": [{"concept": "FEVER", "present": true, "temporality": "past_resolved", "experiencer": "patient", "severity": "unknown", "evidence": "tối thứ hai bị sốt, uống thuốc xong khỏi rồi"}], "unmapped_concerns": []}

Đầu vào: {"text": "nhà em có bà ngoại bị tiểu đường", "ga_weeks": 20, "no_diacritics": false}
JSON: {"findings": [], "unmapped_concerns": []}

Đầu vào:
<<<INPUT>>>
{input_json}
<<<END>>>
Chỉ trả về JSON.
