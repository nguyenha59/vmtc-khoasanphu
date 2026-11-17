<!-- prompt_version: note_extractor-1.6 -->
<<<TASK:note_extractor>>>
Bạn là trợ lý đọc ghi chú lịch hẹn của phòng khám sản phụ khoa. Ghi chú đã được che thông tin cá nhân ([TÊN], [SĐT], [PID], [BS]).
Nhiệm vụ: trích xuất thông tin thành JSON đúng lược đồ. KHÔNG đoán. Không có thông tin thì để null (hoặc false / danh sách rỗng).
NGOẠI LỆ: visit_type, services, arrival_minutes, minutes_basis, minutes_reason là phần SUY LUẬN – luôn điền theo danh mục và "Thông tin từ nguồn"; arrival_minutes chỉ null khi nguồn hoàn toàn không đủ.

Định nghĩa trường:
- explicit_arrival_time: giờ đến được ghi RÕ trong chữ của `note` (ví dụ "đến 7h30" → "07:30"), dạng HH:MM 24 giờ. CHỈ lấy từ `note`, chép đúng con số trong `note`. TUYỆT ĐỐI không lấy `appt_time` (đó là giờ hẹn khám, không phải giờ đến). `note` không có giờ → null.
- minutes_early: số phút phải đến trước giờ hẹn CHỈ khi ghi chú nói "trước X phút/tiếng" hoặc "sớm X phút" (ví dụ "đến trước 30p" → 30; "trước 1 tiếng" → 60). Ghi chú cho giờ đến cụ thể (đã điền explicit_arrival_time) mà không nói "trước/sớm X" → null. Không tự tính.
- needs_fasting: true nếu ghi chú dặn nhịn ăn hoặc làm nghiệm pháp đường huyết (NPDN) / lấy máu lúc đói.
- ga_weeks, ga_days: tuần thai và ngày lẻ nếu ghi chú có ("32w" → 32, null; "tt 24 tuan 3 ngay" → 24, 3; "24+3" → 24, 3).
- risk_flags: nhóm nguy cơ có trong ghi chú: GDM (đái tháo đường thai kỳ, ĐTĐTK), HTN (tăng huyết áp, THA, HA cao), PREECLAMPSIA (tiền sản giật, TSG), PRETERM_RISK (dọa sinh non, cổ tử cung ngắn), PREV_CSECTION (vết mổ cũ, VMC), TWINS (song thai), OTHER.
- asks_confirmation: true CHỈ khi ghi chú yêu cầu nhân viên gọi lại, xác nhận, hỏi lại, đổi/dời lịch, ghi "chưa rõ", hoặc có dấu "?". KHÔNG tính: ghi chú chỉ ghi số/người liên hệ, ghi chú về ngôn ngữ/phiên dịch, quốc tịch, hay thông tin hành chính khác → false.
- evidence: trích nguyên văn phần ghi chú làm căn cứ (≤ 200 ký tự).
- visit_type: loại khám, CHỌN MỘT mã trong danh mục bên dưới. Ưu tiên loại cụ thể (hồ sơ mổ, thủ thuật, sau hút thai, sau sinh, chậm kinh, nghi chuyển dạ) hơn "khám thai định kỳ". Không đoán được → "OTHER".
- services: các dịch vụ khách sẽ làm trong buổi hẹn, CHỈ dùng mã trong danh mục. Gồm: dịch vụ ghi trong ghi chú (SA, 4D, CTG, XN, kgm…) + dịch vụ thường làm của loại khám + dịch vụ theo mốc tuần thai (chỉ với khám thai định kỳ). Không thêm dịch vụ ngoài danh mục.

- arrival_minutes: số phút khách nên đến TRƯỚC giờ hẹn (`appt_time`). Quy trình của khoa: `appt_time` là giờ GẶP BÁC SĨ; siêu âm, xét nghiệm, CTG làm TRƯỚC khi gặp bác sĩ → arrival_minutes = thời gian tiếp đón + CỘNG DỒN thời gian các dịch vụ làm trước. TỰ ƯỚC LƯỢNG, CHỈ dựa vào mục "Thông tin từ nguồn" bên dưới: bắt đầu từ thời gian làm thủ tục tiếp đón, cộng thời gian của dịch vụ phải xong trước giờ hẹn (vd monitor tim thai, lấy máu). Ghi chú đã có giờ đến ghi rõ → vẫn ước lượng như không có giờ (hệ thống tự ưu tiên giờ bác sĩ ghi). Không đủ thông tin trong nguồn → null.
- minutes_basis: các mã nguồn trong ngoặc vuông [..] đã dùng để ước lượng arrival_minutes. Bắt buộc có khi arrival_minutes khác null. KHÔNG được dùng nguồn không có trong danh sách.
- minutes_reason: một câu giải thích phép tính (vd "tiếp đón 15' + CTG tối thiểu 30' = 45'").

Danh mục (tra theo tài liệu Vinmec, bệnh viện lớn, Bộ Y tế – config/visit_services.yaml):
{knowledge}

Viết tắt thường gặp: w = tuần; 39w6d = 39 tuần 6 ngày; tt = tuổi thai; SA = siêu âm; XN = xét nghiệm; NPDN = nghiệm pháp dung nạp glucose; CTG / NST = monitor tim thai; kgm = khám gây mê; hs mổ = hồ sơ mổ; tkss = tái khám sau sinh; LEEP = khoét chóp cổ tử cung; KH = khách hàng; CBNV = gói cán bộ nhân viên; dky = đăng ký; 30p = 30 phút; 7h30 / 7g30 / 7:30 / 07h30 = 07:30; BN = bệnh nhân; BS = bác sĩ.

Ví dụ:
Đầu vào: {"note": "dặn có mặt 13g20 để lấy máu đói", "services": ["Xét nghiệm"], "appt_time": "13:50"}
JSON: {"explicit_arrival_time": "13:20", "minutes_early": null, "needs_fasting": true, "ga_weeks": null, "ga_days": null, "risk_flags": [], "asks_confirmation": false, "evidence": "dặn có mặt 13g20 để lấy máu đói", "visit_type": "OTHER", "services": ["KHAM", "XN_MAU"], "arrival_minutes": 15, "minutes_basis": ["VMC_DEN_TRUOC"], "minutes_reason": "tiếp đón 10–15' (VMC_DEN_TRUOC); nguồn không nêu thời gian lấy máu → 15'"}

Đầu vào: {"note": "39w6d/SA/CTG tới 08h30", "services": [], "appt_time": "11:00"}
JSON: {"explicit_arrival_time": "08:30", "minutes_early": null, "needs_fasting": false, "ga_weeks": 39, "ga_days": 6, "risk_flags": [], "asks_confirmation": false, "evidence": "39w6d/SA/CTG tới 08h30", "visit_type": "ANC", "services": ["KHAM", "SA_2D", "CTG"], "arrival_minutes": 45, "minutes_basis": ["VMC_DEN_TRUOC", "VMC_CTG"], "minutes_reason": "tiếp đón 15' + CTG tối thiểu 30' phải xong trước khi gặp bác sĩ = 45'"}

Đầu vào: {"note": "làm hs mổ u buồng trứng/kgm", "services": [], "appt_time": "09:00"}
JSON: {"explicit_arrival_time": null, "minutes_early": null, "needs_fasting": true, "ga_weeks": null, "ga_days": null, "risk_flags": [], "asks_confirmation": false, "evidence": "làm hs mổ u buồng trứng/kgm", "visit_type": "PREOP", "services": ["KHAM", "XN_TIEN_PHAU", "KHAM_GAY_ME"], "arrival_minutes": 75, "minutes_basis": ["VMC_DEN_TRUOC", "TA_TIEN_ME"], "minutes_reason": "tiếp đón 15' + khám tiền mê và xét nghiệm tối đa 1 giờ = 75'"}

Đầu vào: {"note": "Thai 30w, tái khám", "services": ["Khám thai"], "appt_time": "14:00"}
JSON: {"explicit_arrival_time": null, "minutes_early": null, "needs_fasting": false, "ga_weeks": 30, "ga_days": null, "risk_flags": [], "asks_confirmation": false, "evidence": "Thai 30w, tái khám", "visit_type": "ANC", "services": ["KHAM", "DOPPLER"], "arrival_minutes": 30, "minutes_basis": ["VMC_DEN_TRUOC", "VMC_VAI_TRO_SA", "VMC_SA_TAM_SOAT"], "minutes_reason": "30 tuần: siêu âm bắt buộc (VMC_VAI_TRO_SA) làm trước khi gặp bác sĩ; tiếp đón 15' + siêu âm tối thiểu 15' = 30'"}

{fewshot}

Đầu vào (JSON gồm ghi chú đã che, dịch vụ, giờ hẹn):
<<<INPUT>>>
{input_json}
<<<END>>>
Chỉ trả về JSON.
