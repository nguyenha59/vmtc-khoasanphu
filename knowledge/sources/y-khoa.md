# 10 – Nguồn y khoa cho danh mục cảnh báo form

Tra cứu ngày **11/11/2026**. Mục đích: đối chiếu danh mục `symptom_concepts` trong `config/rules.yaml`
(dùng cho cả lớp quy tắc dò từ ngữ và AI đọc phần tự viết) với các nguồn chính thống.

**Nguyên tắc:**

- Không lưu nguyên văn tài liệu (bản quyền, dung lượng, dễ lỗi thời). Chỉ trích đoạn liên quan + đường dẫn + ngày tra.
- **Thêm cách nói** cho khái niệm đã có = chỉ giúp nhận ra triệu chứng, chỉ có thể *nâng* mức → được áp dụng (mục 4),
  ghi `rules_version` mới, có test.
- **Khái niệm mới, đổi mức, đổi ngưỡng, đổi từ chỉ mức độ nặng** = quy tắc y khoa → **chỉ đề xuất**, chờ bác sĩ
  của khoa duyệt (mục 5). Không tự áp dụng.
- Khái niệm khóa (`SELF_HARM`) không sửa qua đây.

## 1. Nguồn

| Mã | Tổ chức | Tài liệu | Cách trích |
|---|---|---|---|
| S1 | WHO | *Pregnancy, Childbirth, Postpartum and Newborn Care (PCPNC)*, 3rd ed. 2015 (bản in WHO AFRO 2017), mục C15 “Advise on danger signs”, D28, phiếu tư vấn M | Trích nguyên văn từ PDF – [afro.who.int](https://www.afro.who.int/sites/default/files/2019-06/07%2012%2017%20EDITTED%20PCPNC%20GUIDELINES%203RD%20EDITION%202016%20Dec%202017.pdf) |
| S2 | CDC (Hoa Kỳ) | *Hear Her – Urgent Maternal Warning Signs* (trang ghi 15/5/2024) | Trích nguyên văn – [cdc.gov/hearher](https://www.cdc.gov/hearher/maternal-warning-signs/index.html) |
| S3 | ACOG / AIM | *Urgent Maternal Warning Signs* | Trích nguyên văn – [obgynsafety.org](https://obgynsafety.org/urgent-maternal-warning-signs/) |
| S4 | NICE (Anh) | NG133 *Hypertension in pregnancy*, khuyến nghị 1.1.1 | Qua kết quả tìm kiếm (trang gốc chặn truy cập tự động) – [nice.org.uk](https://www.nice.org.uk/guidance/ng133/chapter/recommendations) – **cần đối chiếu lại** |
| S5 | Bộ Y tế VN | QĐ 4128/QĐ-BYT (29/7/2016) *Hướng dẫn quốc gia về các dịch vụ chăm sóc sức khỏe sinh sản* | Qua bản tóm tắt – [luatvietnam.vn](https://luatvietnam.vn/y-te/quyet-dinh-4128-qd-byt-2016-ve-viec-phe-duyet-tai-lieu-huong-dan-quoc-gia-ve-dich-vu-cham-soc-suc-khoe-sinh-san-319200-d1.html) – **cần đối chiếu bản gốc**; có văn bản 2026 (QĐ 1139, [vnpa.moh.gov.vn](https://vnpa.moh.gov.vn/wp-content/uploads/2026/05/QD-1139-Tai-lieu-huong-dan-cham-soc-SKSS.pdf)) có thể thay phần chăm sóc trước sinh – khoa xác nhận văn bản hiện hành |

## 2. Trích đoạn

**S1 – WHO PCPNC, khi mang thai (C15):** “Advise to go to the hospital/health centre immediately, day or night,
WITHOUT waiting if she experiences any of the following signs: vaginal bleeding; convulsions; severe headaches with
blurred vision; fever and too weak to get out of bed; severe abdominal pain; fast or difficult breathing.
She should go to the health centre as soon as possible if: fever; abdominal pain; feels ill; swelling of fingers, face, legs.”
Phiếu tư vấn thêm: “water breaks and not in labour” (đi sớm).

**S1 – sau sinh (D28):** đi ngay nếu “vaginal bleeding: more than 2 or 3 pads soaked in 20–30 minutes after delivery OR
bleeding increases rather than decreases; convulsions; fast or difficult breathing; fever and too weak to get out of bed;
severe abdominal pain; calf pain, redness or swelling, shortness of breath or chest pain.” Đi sớm nếu “fever; abdominal pain;
feels ill; breasts swollen, red or tender, or sore nipple; urine dribbling or pain on micturition; pain in the perineum or
draining pus; foul-smelling lochia”, kèm “severe depression or suicidal behaviour (ideas or attempts)”.

**S2 – CDC (khi mang thai và tới 1 năm sau sinh), “seek medical care immediately”:** headache that won’t go away or gets
worse over time; dizziness or fainting; changes in your vision; fever of 100.4°F (38°C) or higher; extreme swelling of
your hands or face; thoughts about harming yourself or your baby; trouble breathing; chest pain or fast-beating heart;
severe nausea and throwing up; severe belly pain that doesn’t go away; baby’s movement stopping or slowing during pregnancy;
vaginal bleeding or fluid leaking during pregnancy; vaginal bleeding or discharge after pregnancy; severe swelling, redness,
or pain of your leg or arm; overwhelming tiredness.

**S3 – ACOG/AIM:** cùng 15 dấu hiệu như S2 (ghi “severe nausea and throwing up (not like morning sickness)”);
“contact your provider right away. If you can’t reach your provider, go to the nearest emergency room.”

**S4 – NICE NG133 1.1.1 (dấu hiệu tiền sản giật, cần gặp nhân viên y tế ngay):** severe headache; problems with vision such as
blurring or flashing before the eyes; severe pain just below the ribs; vomiting; sudden swelling of the face, hands or feet.

**S5 – Bộ Y tế 4128/2016 (qua bản tóm tắt):** “Nhức đầu, hoa mắt, đau thượng vị, nôn mửa (dấu hiệu tiền sản giật)”;
“dấu hiệu báo động cần thăm lại ngay như đau bụng, ra huyết và phù nề”; “mệt mỏi, uể oải, đau đầu, ăn kém ngon (dấu hiệu thiếu máu)”.

## 3. Đối chiếu danh mục hiện có (rules_version 0.4.0)

| Khái niệm | Mức hiện tại | Nguồn nói gì | Nhận xét |
|---|---|---|---|
| BLEEDING – Ra máu âm đạo | Đỏ | S1 đi ngay; S2, S3 ngay; S5 báo động | Khớp. Thiếu cách nói “máu cục”, “thấm ướt băng” (S1 sau sinh) |
| REDUCED_FM – Giảm cử động thai | Đỏ | S2, S3 “stopping or slowing” | Khớp. Thiếu “con không đạp”, “đạp yếu” |
| VISUAL – Rối loạn thị giác | Đỏ | S1 (kèm đau đầu), S2, S4 “blurring or flashing”, S5 “hoa mắt” | Khớp. Thiếu “nhìn nhòe”, “lóa mắt”, “nhìn đôi” |
| EPIGASTRIC – Đau thượng vị | Đỏ | S4 “severe pain just below the ribs”, S5 | Khớp. Thiếu “đau dưới sườn”, “đau hạ sườn phải” |
| HEADACHE – Đau đầu nhiều | **Đỏ với mọi câu có “đau đầu”** | S1 “severe headaches with blurred vision”; S2 “won’t go away or gets worse”; S4 “severe” | **Lệch:** nguồn chỉ coi là khẩn khi nặng / kéo dài / kèm nhìn mờ → xem đề xuất **P01** |
| FLUID – Ra nước âm đạo | Đỏ | S2, S3 “fluid leaking”; S1 “water breaks” | Khớp. Thiếu “rỉ nước”, “vỡ nước ối” |
| CONTRACTIONS – Gò bụng | Đỏ trước 37 tuần | (không nằm trong danh sách S1–S3 như một dấu hiệu riêng) | Giữ theo khoa (đã có mục [CẦN XÁC NHẬN]) |
| ABD_PAIN – Đau bụng | Vàng; Đỏ nếu nặng | S1 “severe abdominal pain” đi ngay, “abdominal pain” đi sớm; S2 “severe … doesn’t go away” | Khớp cấu trúc. Từ chỉ mức nặng thiếu “không đỡ”, “liên tục” → **P02** |
| DYSPNEA – Khó thở | Đỏ | S1 “fast or difficult breathing”; S2, S3 | Khớp. Thiếu “thở nhanh”, “thở dốc”, “thở gấp” |
| CALF – Đau sưng một bên bắp chân | Đỏ | S1 sau sinh “calf pain, redness or swelling”; S2 “leg or arm” | Khớp. Thiếu “chân sưng đỏ”; S2 có cả tay → **P07** |
| FACE_EDEMA – Phù mặt / tay | Vàng | S1 “as soon as possible”; S2 “extreme swelling” ngay; S4 “sudden swelling” | Mức vàng khớp S1; S2/S4 nghiêng về khẩn khi phù đột ngột/nhiều → **P06** |
| FEVER – Sốt | Vàng | S1: sốt = đi sớm, sốt + mệt không dậy nổi = đi ngay; S2: ≥ 38°C ngay | Khớp S1; kết hợp “sốt + li bì” chưa có → **P05** |
| DYSURIA – Tiểu buốt | Vàng | S1 sau sinh “pain on micturition” đi sớm | Khớp |
| ITCH – Ngứa lòng bàn tay/chân | Vàng | (không có trong S1–S3) | Giữ theo khoa |
| HYPEREMESIS – Nghén nặng | Vàng | S2 “severe nausea and throwing up”; S3 “not like morning sickness”; S4 “vomiting” (tiền sản giật) | Khớp mức vàng. Thiếu “ói nhiều”, “nôn liên tục” |
| SELF_HARM – Ý nghĩ tự hại | Đỏ, **khóa** | S1 “suicidal behaviour”; S2 “harming yourself **or your baby**” | Thiếu ý “làm hại con” → **P08** (khái niệm khóa, chỉ đề xuất) |

**Dấu hiệu nguồn có mà danh mục chưa có:** co giật (S1 ngay), đau ngực / tim đập nhanh (S1 sau sinh, S2, S3),
chóng mặt / ngất (S2, S3), sản dịch hôi (S1), vú sưng đỏ đau (S1), mệt lả (S2, S3) → **P03, P04, P09–P11**.

## 4. Đã áp dụng: thêm cách nói cho khái niệm đã có

Chỉ thêm vào **cuối** danh sách `synonyms` (prompt AI chỉ dùng 6 cách nói đầu nên không đổi prompt; lớp dò danh mục
dùng toàn bộ). Mỗi cụm có dạng không dấu. Áp dụng ở `rules_version` **0.5.0**, sau khi bài đo Ollama 0.4.0 chạy xong.

| Khái niệm | Thêm | Nguồn |
|---|---|---|
| BLEEDING | máu cục, ra cục máu, rỉ máu, thấm ướt băng, ướt đẫm băng | S1 (sau sinh: thấm 2–3 băng/20–30 phút; máu tăng) |
| REDUCED_FM | con không đạp, bé không đạp, thai không máy, đạp yếu | S2, S3 |
| VISUAL | nhìn nhòe, mắt nhòe, lóa mắt, nhìn đôi, nhìn không rõ | S2, S4 (blurring or flashing), S5 |
| EPIGASTRIC | đau dưới sườn, đau hạ sườn phải, đau dưới xương sườn | S4 |
| FLUID | rỉ nước, vỡ nước ối, nước chảy ra âm đạo | S1, S2, S3 |
| DYSPNEA | thở nhanh, thở dốc, thở gấp | S1 (fast or difficult breathing) |
| CALF | chân sưng đỏ, sưng đỏ một bên chân | S1 (redness or swelling), S2 |
| FACE_EDEMA | sưng mí mắt, mí mắt sưng | S2, S4 (swelling of the face) |
| HYPEREMESIS | ói nhiều, nôn liên tục, nôn ói nhiều | S2, S3 |

## 5. Đề xuất chờ bác sĩ duyệt (không tự áp dụng)

Bác sĩ chọn phương án cho từng mục; sau khi duyệt, đổi qua quy trình cấu hình (tăng `rules_version`, chạy lại
`scripts/run_eval.py --dataset forms`, độ nhạy ca đỏ phải giữ 100%).

| Mã | Nội dung | Nguồn | Phương án |
|---|---|---|---|
| P01 | **Đau đầu**: hiện mọi câu có “đau đầu” → đỏ | S1, S2, S4 chỉ khẩn khi nặng/kéo dài/kèm nhìn mờ | (a) giữ nguyên – an toàn, nhiều báo nhầm; (b) như ABD_PAIN: vàng, đỏ khi có từ nặng (“dữ dội”, “không đỡ”, “uống thuốc không hết”, “kèm nhìn mờ”); (c) đỏ khi ≥ 20 tuần, vàng trước 20 tuần |
| P02 | Từ chỉ mức nặng của đau bụng: thêm “không đỡ”, “không hết”, “liên tục” | S2 “doesn’t go away” | duyệt / không |
| P03 | Khái niệm mới **CONVULSION – Co giật** (co giật, lên cơn, giật tay chân, sùi bọt mép) | S1 đi ngay | đề xuất mức **đỏ** |
| P04 | Khái niệm mới **CHEST_PAIN – Đau ngực / tim đập nhanh** (đau ngực, tức ngực, tim đập nhanh, hồi hộp đánh trống ngực) | S1 sau sinh, S2, S3 | đỏ / vàng |
| P05 | **Sốt kèm mệt không dậy nổi** → đỏ (sốt đơn thuần giữ vàng) | S1 | duyệt / không |
| P06 | Phù mặt/tay **đột ngột** hoặc **nhiều** → đỏ | S2, S4 | duyệt / không |
| P07 | CALF mở rộng sang **tay** (sưng đỏ đau một bên tay) | S2 | duyệt / không |
| P08 | SELF_HARM thêm ý **làm hại con** (“muốn làm hại con”, “sợ mình làm hại bé”) | S2, S3 | khái niệm khóa – cần bác sĩ + người phụ trách an toàn |
| P09 | Khái niệm mới **SYNCOPE – Chóng mặt / ngất** (ngất, xỉu, choáng, tối sầm mặt mũi) | S2, S3 | đỏ (ngất) / vàng (chóng mặt) |
| P10 | Khái niệm mới sau sinh: **sản dịch hôi**, **vú sưng đỏ đau** | S1 | vàng |
| P11 | Khái niệm mới **mệt lả** (mệt không đứng dậy nổi, kiệt sức) | S2, S3 | vàng |

| P12 | **Danh mục quá rộng gây báo đỏ nhầm** (rà soát 11/11/2026, đã chạy thử): “chuột rút **bắp chân**” → Đau sưng bắp chân (đỏ); “em **nằm im** nghỉ cả ngày” → Giảm thai máy (đỏ); “**chảy máu** chân răng” → Ra máu âm đạo (đỏ); “**ra nước** mũi” → Ra nước âm đạo (đỏ); “hơi **nhức đầu** vì thiếu ngủ” → Đau đầu nhiều (đỏ – xem P01). Lớp danh mục chỉ nâng nên AI đọc đúng cũng không hạ được | — | (a) thêm cụm loại trừ cho từng khái niệm (`chân răng`, `mũi`, `chuột rút`, `nằm im nghỉ`…) – **đề xuất**, không giảm độ nhạy; (b) chỉ danh mục thấy, AI chạy tốt và 3/3 lần nói không có → Vàng + cần người xem; (c) giữ nguyên |

Câu hỏi form cũng có thể cần thêm (ví dụ “Chị có co giật / ngất không?”) – thuộc `config/forms.yaml`, bác sĩ duyệt nội dung.

## 6. Vòng học (phương án A)

Sau khi áp dụng, màn **Chất lượng AI → “Học từ kết quả gọi”** thống kê theo từng lý do cảnh báo: bao nhiêu ca đã gọi,
giữ mức / hạ vàng / báo nhầm, và **so theo `rules_version`** (0.4.0 trước – 0.5.0 sau). Lý do có tỷ lệ báo nhầm cao
(ngưỡng trong `config/eval_targets.yaml → feedback`) được đưa vào danh sách “Cần bác sĩ xem lại” – đó là đầu vào cho
lần duyệt danh mục tiếp theo. Hệ thống không tự sửa quy tắc.

## 6. Đề xuất thêm cách nói (12/11/2026) – CHỜ BÁC SĨ DUYỆT, chưa áp dụng
Từ bộ đo ngoài phân phối (`tests/benchmark/ood_v1`): lớp quy tắc (không AI) bỏ sót gần hết cách nói khẩu ngữ (độ nhạy ca đỏ 0%).
Thêm cách nói giúp quy tắc vẫn bắt được khi AI lỗi. Đây là cách nói dân gian cho khái niệm ĐÃ CÓ, chưa có nguồn chính thống
trích nguyên văn → để bác sĩ duyệt. LƯU Ý: áp dụng xong thì ood_v1/ood_v2 không còn là bộ đo kín – cần soạn bộ kín mới.

| Khái niệm | Cách nói đề xuất (kèm dạng không dấu) |
|---|---|
| EPIGASTRIC | mỏ ác, đau mỏ ác, đau vùng dạ dày |
| FEVER | hâm hấp, người hâm hấp, ớn lạnh |
| VISUAL | chấm sáng trước mắt, lóa lóa, nhìn thành hai |
| REDUCED_FM | ít cựa quậy, không cựa quậy, ít cử động |
| DYSPNEA | thở hổn hển, thở dốc, không ra hơi |
| DYSURIA | tiểu rát, tiểu xót, đi tiểu rát |
| HYPEREMESIS | ói nhiều, ăn gì nôn nấy, nôn thốc nôn tháo |
| FACE_EDEMA | mí mắt húp, mặt phồng |

Đã áp dụng (không phải quy tắc mới): mô tả ý nghĩa `meaning` cho 16 khái niệm, diễn giải dấu hiệu theo S1–S4,
chỉ đưa vào prompt AI (form_reader-1.4) – không đổi lớp quy tắc, không đổi mức.

## 7. Rà soát "khi nào báo Đỏ" (15/11/2026, rules_version 0.16.0)

**Nguyên tắc:** mọi cảnh báo Đỏ phải chỉ ra được *dữ liệu nào* (câu trả lời / câu thai phụ viết / số đo) → *quy tắc nào* →
*nguồn nào* (S1–S5 hoặc "quy tắc của khoa"). Nguồn khai báo trong `config/rules.yaml → red_basis`, hiện ở mục
"Vì sao ca này khẩn" (dòng **Căn cứ**). Test: `tests/test_red_basis.py`.

**Đã áp dụng theo lựa chọn của người dùng:**

| Mã | Quyết định |
|---|---|
| P01 | Đau đầu (phần tự viết) = **Vàng**; **Đỏ** khi nặng / không đỡ / kéo dài (`severe_words`), hoặc kèm nhìn mờ / đau thượng vị / phù mặt-tay (`text_combos`), hoặc HA ≥ 140/90 cùng form (`red_with_bp`). Câu có cấu trúc C6 giữ Đỏ (câu hỏi gộp đau đầu nhiều / nhìn mờ / đau thượng vị). |
| P03 | Thêm CONVULSION – Co giật = Đỏ (S1) |
| P04 | Thêm CHEST_PAIN – Đau ngực / tim đập nhanh = Đỏ (S2, S3); loại "căng tức ngực" (vú) |
| P09 | Thêm SYNCOPE – Ngất = Đỏ; DIZZINESS – Chóng mặt = Vàng (S2, S3) |

**Sửa lỗi đọc nhầm (đọc sai vị trí / chủ thể – cùng loại với "chảy máu chân răng" đã có):** "chảy máu cam", "ra nước mũi /
nước mắt / nước bọt", "em (mẹ) nằm im nghỉ" → không còn là Ra máu / Ra nước / Giảm thai máy. Câu chỉ kể thông tin đọc / nghe
được ("đọc trên mạng thấy ra máu…") → **Vàng, gọi xác minh** (không về Xanh). Gò bụng ghi nguồn "quy tắc của khoa" (không
nằm trong S1–S3).

**Kết quả chạy thử 48 câu kiểu thai phụ hay viết (dữ liệu giả), AI cục bộ qwen2.5:3b:**

| | Trước | Sau |
|---|---|---|
| Báo Đỏ nhầm | 9 | 2 |
| Bỏ sót / hạ thấp ca nguy hiểm (co giật, ngất, đau thượng vị) | 3 | 0 |
| Vàng thừa (an toàn) | 7 | 7 |

**Đỏ do chỉ AI thấy (người dùng chọn phương án b, 15/11/2026, rules 0.17.0):** khái niệm Đỏ mà lớp danh mục không thấy từ
khóa nào trong câu (vd "mắt em hơi mỏi" → AI đọc thành Rối loạn thị giác) → **Vàng + "AI nghi ngờ – gọi xác minh"**
(`form_cap.ai_only_red_cap`). Không áp dụng cho SELF_HARM (khóa, luôn Đỏ). Đánh đổi: cách nói dân gian chỉ AI hiểu được sẽ
được gọi trong ca thay vì gọi ngay → bổ sung cách nói vào danh mục khi phát hiện (đã thêm "bụng cứng", "bụng căng cứng").

**Còn chờ quyết định:** (2) khó thở khi gắng sức, nghỉ thì hết – Đỏ hay Vàng; (3) thêm cách nói gián
tiếp cho SELF_HARM ("sống chẳng còn ý nghĩa") – khái niệm khóa, cần bác sĩ + người phụ trách an toàn (AI hiện vẫn bắt được).

### 7.x Người dùng xác nhận giữ nguyên (15/11/2026, rules_version 0.17.2)

- "Chóng mặt muốn xỉu" (gần ngất) → Vàng, không tính như ngất.
- "Đôi khi em nghĩ tới cái chết" → Đỏ nhờ mô hình AI; SELF_HARM khóa, chưa thêm cách nói gián tiếp vào từ điển (chờ bác sĩ).
- "Buồn chán, không muốn làm gì" bị AI hiểu thành SELF_HARM → Đỏ: chấp nhận (lệch về an toàn).
- "Tuần trước em co giật một lần" → Đỏ (thời điểm quá khứ chưa chứng minh đã hết).
- Câu hỏi kiến thức ("ra máu báo thai có nguy hiểm không") → Vàng + gọi xác minh.
Khóa bằng test: tests/test_review_fixes_20261115.py::test_confirmed_levels_20261115.

## 8. Nguồn cho danh mục loại khám → dịch vụ → giờ đến (17/11/2026)

Dùng trong `config/visit_services.yaml` (file OH không có cột Dịch vụ). Bài Vinmec / Tâm Anh là bài phổ biến kiến thức, **không phải quy trình nội bộ**; số phút đến trước không có trong nguồn công khai nào ngoài "đến trước 10–15 phút" của Vinmec → mọi số ngoài 30/20/15' khoa đã chốt đều `[CẦN XÁC NHẬN]`. Không dùng văn bản chỉ đọc được trích yếu (QĐ 2818/2026, QĐ 1359/2026, TT 12/2026) làm căn cứ.

| Mã | Nguồn | Link |
|---|---|---|
| VMC_DEN_TRUOC | Vinmec – Hướng dẫn đặt lịch trên Online Vinmec: đến trước giờ hẹn 10–15 phút | https://www.vinmec.com/vie/bai-viet/huong-dan-dat-lich-kham-vinmec-tren-website-online-vinmec |
| VMC_3_THANG_CUOI | Vinmec – Lịch khám thai chi tiết 3 tháng cuối (monitor tim thai + cơn co tuần 37–40) | https://www.vinmec.com/vie/bai-viet/lich-kham-thai-chi-tiet-3-thang-cuoi-cho-me-bau-vi |
| VMC_GOI_THAI_SAN | Vinmec – Bảng quyền lợi các chương trình thai sản 12/22/27/36 tuần (PDF) | https://www.vinmec.com/static/uploads/FLYER_BANGGIAKHOASANG_VN_EDIT_2_2f801a0ed5.pdf |
| BYT_1807 | QĐ 1807/QĐ-BYT 2020 – Hướng dẫn chuyên môn sàng lọc, chẩn đoán, điều trị trước sinh và sơ sinh (PDF) | https://phongdanso.dongnai.gov.vn/wp-content/uploads/2024/04/QD-1807_BYT_Huong-dan-chuyen-mon-ky-thuat-trong-sang-loc-chan-doan-dieu-tri-truoc-sinh-va-so-sinh.pdf |
| TA_MOC_KHAM | Tâm Anh – Lịch đi khám thai định kỳ (XN nước tiểu mỗi lần, nhịn ăn 12 giờ khi có XN máu – chỉ tham khảo) | https://tamanhhospital.vn/lich-di-kham-thai-dinh-ky-cho-bau/ |
| VMC_TRUOC_GOI | Vinmec – Trước khi mua gói thai sản (mỗi lần khám: thử nước tiểu nhanh, cân nặng, huyết áp, gặp bác sĩ) | https://www.vinmec.com/vie/bai-viet/truoc-khi-mua-goi-thai-san-co-can-tham-kham-va-kiem-tra-voi-bac-si-san-khong-vi |
| VMC_22_TUAN | Vinmec – 22 tuần: thời điểm tốt nhất để khảo sát dị tật thai nhi | https://www.vinmec.com/vi/tin-tuc/thong-tin-suc-khoe/san-phu-khoa-va-ho-tro-sinh-san/22-tuan-thoi-diem-tot-nhat-de-khao-sat-di-tat-thai-nhi/ |
| KHOA_1211 | Khoa xác nhận số phút đến sớm 12/11/2026 (docs/04 A3) | — |
| VMC_MOC_KHAM | Vinmec – Các mốc khám thai định kỳ quan trọng | https://www.vinmec.com/vie/bai-viet/cac-moc-kham-thai-dinh-ky-quan-trong-vi |
| VMC_10_MOC | Vinmec – 10 mốc khám thai bà bầu cần ghi nhớ | https://www.vinmec.com/vie/bai-viet/10-moc-kham-thai-ba-bau-can-ghi-nho-vi |
| VMC_DOUBLE | Vinmec – Xét nghiệm Double test được thực hiện vào tuần thai nào? | https://www.vinmec.com/vie/bai-viet/xet-nghiem-ouble-test-duoc-thuc-hien-vao-tuan-thai-nao-vi |
| VMC_SA_4D | Vinmec – 3 thời điểm cần siêu âm 4D: 11–13, 20–22, 30–32 tuần | https://vinmec.com/eng/article/3-times-when-4d-pregnancy-ultrasound-is-needed-11-13-weeks-20-22-weeks-30-32-weeks-en |
| VMC_OGTT | Vinmec – Vì sao mọi phụ nữ mang thai đều nên kiểm tra nguy cơ tiểu đường thai kỳ (nhịn đói 8 giờ, đo 0–1–2 giờ) | https://www.vinmec.com/vie/bai-viet/vi-sao-moi-phu-nu-mang-thai-deu-nen-kiem-tra-nguy-co-tieu-duong-thai-ky-vi |
| BYT_1470 | QĐ 1470/QĐ-BYT 2024 – Hướng dẫn quốc gia ĐTĐ thai kỳ: NPDN 75 g tuần 24–28, nhịn đói ≥ 8 giờ | — |
| VMC_GBS | Vinmec – Nhiễm liên cầu khuẩn nhóm B ở thai 35–37 tuần | https://www.vinmec.com/vie/bai-viet/nhiem-lien-cau-khuan-nhom-b-o-phu-nu-co-thai-ky-35-37-tuan-va-phac-do-dieu-tri-vi |
| VMC_NST | Vinmec – Xét nghiệm Non-stress test là gì? (theo dõi khoảng 60 phút) | https://www.vinmec.com/vie/bai-viet/xet-nghiem-non-stress-test-la-gi-vi |
| VMC_CTG | Vinmec – Theo dõi sức khỏe thai bằng chỉ số CTG (tối thiểu 30 phút) | https://www.vinmec.com/vie/bai-viet/theo-doi-suc-khoe-thai-bang-chi-so-ctg-vi |
| TA_TIEN_ME | Tâm Anh – Khám tiền mê (XN máu, đông máu, đường huyết; 15 phút–1 giờ) | https://tamanhhospital.vn/kham-tien-me/ |
| VMC_TIEN_PHAU | Vinmec – Đánh giá tiền phẫu trong gây mê | https://www.vinmec.com/vie/bai-viet/danh-gia-tien-phau-trong-gay-me-vi |
| VMC_LEEP | Vinmec – Khoét chóp cổ tử cung được chỉ định khi nào (nhịn ăn ≥ 8 tiếng) | https://www.vinmec.com/vie/bai-viet/khoet-chop-co-tu-cung-duoc-chi-dinh-khi-nao-va-thuc-hien-ra-sao-vi |
| VMC_SOI_TUOI | Vinmec – Những điều cần biết về soi tươi dịch âm đạo | https://www.vinmec.com/vie/bai-viet/nhung-dieu-can-biet-ve-soi-tuoi-dich-am-dao-vi |
| VMC_BHCG | Vinmec – Ý nghĩa xét nghiệm Beta HCG trong chẩn đoán có thai | https://www.vinmec.com/vie/bai-viet/y-nghia-xet-nghiem-beta-hcg-trong-chan-doan-co-thai-vi |
| VMC_SAU_SINH | Vinmec – Các kiểm tra mẹ cần làm sau 6 tuần sinh con | https://www.vinmec.com/vie/bai-viet/cac-kiem-tra-me-can-lam-sau-6-tuan-sinh-con-vi |
| MSD_PHA_THAI | MSD Manual – Nạo phá thai (xác nhận hết thai bằng siêu âm / beta-hCG) | https://www.msdmanuals.com/vi/professional/phụ-khoa-và-sản-khoa/kế-hoạch-hóa-gia-đình/nạo-phá-thai |
