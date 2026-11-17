# Schema dữ liệu – Trợ lý lịch hẹn & theo dõi thai kỳ

Sinh tự động bởi `scripts/export_mock_package.py`.

## 1. File lịch hẹn xuất từ OH (đầu vào)

| Cột trong file | Trường chuẩn | Mô tả |
|---|---|---|
| `HN` | `pid` | Mã bệnh nhân. Khách tự đặt (Autobooking) có thể trống → mã tạm TAM-… + Cần xác nhận |
| `Patient Name` | `full_name` | Họ tên (mã hóa khi lưu) |
| `Appointment Start Date Time` | `appt_datetime` | Ngày + giờ hẹn trong một ô (giờ GẶP BÁC SĨ) |
| `Appointment Resource` | `doctor` | Tên bác sĩ |
| `Appointment Comment` | `note` | Ghi chú – nguồn DUY NHẤT để suy ra loại khám, dịch vụ, tuần thai, giờ dặn đến |
| `Mobile` | `phone` | Số điện thoại (mã hóa khi lưu, hiển thị dạng che) |

Bắt buộc: `pid`, `full_name`, `phone`, `appt_datetime|appt_date`, `appt_datetime|start_time`, `note` (`a|b` = một trong hai).
Ghi chú khách tự đặt: tiền tố `Autobooking_…`; phần kênh / cộng tác viên / mã đơn được cắt bỏ trước khi đọc.

## 2. Cơ sở dữ liệu (core/models.py)

Trường có hậu tố `_enc` được mã hóa Fernet (khóa `APP_SECRET_KEY`).

### `arrival`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `pid` | VARCHAR(32) | bắt buộc, index |
| `visit_date` | DATE | bắt buộc, index |
| `checkin_time` | TIME |  |
| `batch_id` | INTEGER |  |

### `audit_log`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `ts` | DATETIME | bắt buộc, index |
| `user_id` | INTEGER |  |
| `action` | VARCHAR(64) | bắt buộc |
| `entity` | VARCHAR(64) |  |
| `entity_id` | VARCHAR(64) |  |
| `details` | JSON | bắt buộc |

### `eval_run`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `ts` | DATETIME | bắt buộc |
| `dataset` | VARCHAR(32) | bắt buộc |
| `prompt_version` | VARCHAR(64) |  |
| `rules_version` | VARCHAR(16) |  |
| `model` | VARCHAR(64) |  |
| `metrics` | JSON | bắt buộc |

### `import_batch`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `kind` | VARCHAR(16) | bắt buộc |
| `file_name` | VARCHAR(256) | bắt buộc |
| `row_count` | INTEGER | bắt buộc |
| `errors` | JSON | bắt buộc |
| `target_date` | DATE |  |
| `created_by` | INTEGER |  |
| `created_at` | DATETIME | bắt buộc |

### `message_log`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `channel` | VARCHAR(32) | bắt buộc |
| `template` | VARCHAR(64) | bắt buộc |
| `patient_code` | VARCHAR(16) |  |
| `status` | VARCHAR(16) | bắt buộc |
| `error` | TEXT |  |
| `created_at` | DATETIME | bắt buộc |

### `patient`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `code` | VARCHAR(16) | index |
| `pid` | VARCHAR(32) | bắt buộc, index |
| `full_name_enc` | TEXT | bắt buộc |
| `phone_enc` | TEXT | bắt buộc |
| `nationality` | VARCHAR(64) |  |
| `consent_status` | VARCHAR(16) | bắt buộc |
| `consent_at` | DATETIME |  |
| `consent_by` | INTEGER |  |
| `risk_groups` | JSON | bắt buộc |
| `proposed_risk_flags` | JSON | bắt buộc |
| `on_insulin` | BOOLEAN | bắt buộc |
| `edd` | DATE |  |
| `delivery_date` | DATE |  |

### `rule_change`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `key` | VARCHAR(128) | bắt buộc |
| `old_value` | JSON | bắt buộc |
| `new_value` | JSON | bắt buộc |
| `reason` | TEXT |  |
| `proposed_by` | INTEGER |  |
| `proposed_at` | DATETIME |  |
| `status` | VARCHAR(16) | bắt buộc |
| `decided_by` | INTEGER |  |
| `decided_at` | DATETIME |  |
| `new_rules_version` | VARCHAR(16) |  |

### `user`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `username` | VARCHAR(64) | bắt buộc |
| `name` | VARCHAR(128) | bắt buộc |
| `role` | VARCHAR(16) | bắt buộc |

### `user_session`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `sid_hash` | VARCHAR(64) | PK |
| `username` | VARCHAR(64) | bắt buộc, index |
| `user_id` | INTEGER | bắt buộc |
| `created_at` | DATETIME | bắt buộc |
| `expires_at` | DATETIME | bắt buộc |
| `revoked_at` | DATETIME |  |

### `appointment`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `patient_id` | INTEGER | FK → patient.id, bắt buộc, index |
| `appt_date` | DATE | bắt buộc, index |
| `start_time` | TIME | bắt buộc |
| `end_time` | TIME |  |
| `services` | JSON | bắt buộc |
| `department` | VARCHAR(128) |  |
| `doctor` | VARCHAR(128) |  |
| `note_enc` | TEXT |  |
| `booking_source` | VARCHAR(64) |  |
| `booked_at` | DATETIME |  |
| `batch_id` | INTEGER | FK → import_batch.id |
| `merged_from` | JSON | bắt buộc |
| `row_warnings` | JSON | bắt buộc |

### `form_plan_decline`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `patient_id` | INTEGER | PK, FK → patient.id |
| `code` | VARCHAR(16) | PK |
| `edd` | DATE |  |
| `declined_by` | INTEGER |  |
| `declined_at` | DATETIME | bắt buộc |

### `form_response`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `token` | VARCHAR(32) |  |
| `patient_id` | INTEGER | FK → patient.id, bắt buộc, index |
| `form_code` | VARCHAR(16) | bắt buộc |
| `submitted_at` | DATETIME | bắt buộc, index |
| `answers` | JSON | bắt buộc |
| `free_text_enc` | TEXT |  |
| `source` | VARCHAR(16) | bắt buộc |

### `form_schedule`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `patient_id` | INTEGER | FK → patient.id, bắt buộc, index |
| `send_date` | DATE | bắt buộc, index |
| `form_code` | VARCHAR(16) |  |
| `reason` | TEXT |  |
| `status` | VARCHAR(16) | bắt buộc |
| `skip_reason` | TEXT |  |
| `token` | VARCHAR(32) |  |
| `rules_version` | VARCHAR(16) |  |

### `form_token`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `token` | VARCHAR(32) | PK |
| `patient_id` | INTEGER | FK → patient.id, bắt buộc, index |
| `form_code` | VARCHAR(16) | bắt buộc |
| `created_at` | DATETIME | bắt buộc |
| `expires_at` | DATETIME | bắt buộc |
| `used_at` | DATETIME |  |
| `reminded_at` | DATETIME |  |

### `patient_history`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `patient_id` | INTEGER | PK, FK → patient.id |
| `data_enc` | TEXT | bắt buộc |
| `source` | VARCHAR(16) | bắt buộc |
| `updated_at` | DATETIME | bắt buộc |

### `patient_program`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `patient_id` | INTEGER | PK, FK → patient.id |
| `in_package` | BOOLEAN | bắt buộc |
| `package_name` | VARCHAR(128) |  |
| `package_since` | DATE |  |
| `override_monitoring` | BOOLEAN | bắt buộc |
| `override_reason` | VARCHAR(300) |  |
| `updated_by` | INTEGER |  |
| `updated_at` | DATETIME |  |

### `question_selection`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `token` | VARCHAR(32) | PK |
| `patient_id` | INTEGER | FK → patient.id, bắt buộc, index |
| `created_at` | DATETIME | bắt buộc |
| `method` | VARCHAR(8) | bắt buộc |
| `questions` | JSON | bắt buộc |
| `model` | VARCHAR(64) |  |
| `prompt_version` | VARCHAR(64) |  |
| `rules_version` | VARCHAR(16) | bắt buộc |
| `confidence` | FLOAT |  |
| `raw` | JSON | bắt buộc |
| `error` | TEXT |  |

### `vital_reading`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `patient_id` | INTEGER | FK → patient.id, bắt buộc, index |
| `measured_at` | DATETIME | bắt buộc, index |
| `kind` | VARCHAR(16) | bắt buộc |
| `value` | FLOAT | bắt buộc |
| `source_response_id` | INTEGER |  |

### `arrival_plan`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `appointment_id` | INTEGER | FK → appointment.id, bắt buộc, index |
| `arrival_time` | TIME |  |
| `proposed_time` | TIME |  |
| `method` | VARCHAR(16) | bắt buộc |
| `confidence` | FLOAT |  |
| `status` | VARCHAR(16) | bắt buộc |
| `reasons` | JSON | bắt buộc |
| `details` | JSON | bắt buộc |
| `rules_version` | VARCHAR(16) | bắt buộc |
| `confirmed_by` | INTEGER |  |
| `confirmed_at` | DATETIME |  |

### `assessment`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `response_id` | INTEGER | FK → form_response.id, bắt buộc, index |
| `level` | VARCHAR(8) | bắt buộc |
| `rule_level` | VARCHAR(8) | bắt buộc |
| `trend_level` | VARCHAR(8) | bắt buộc |
| `ai_level` | VARCHAR(8) | bắt buộc |
| `ai_confidence` | FLOAT |  |
| `findings` | JSON | bắt buộc |
| `summary` | TEXT |  |
| `rules_version` | VARCHAR(16) | bắt buộc |
| `prompt_version` | VARCHAR(64) |  |
| `model` | VARCHAR(64) |  |
| `raw` | JSON | bắt buộc |
| `created_at` | DATETIME |  |

### `noshow_prediction`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `appointment_id` | INTEGER | FK → appointment.id, bắt buộc, index |
| `prob` | FLOAT | bắt buộc |
| `top_factors` | JSON | bắt buộc |
| `model_version` | VARCHAR(32) | bắt buộc |
| `created_at` | DATETIME | bắt buộc |

### `note_extraction`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `appointment_id` | INTEGER | FK → appointment.id, bắt buộc, index |
| `ga_weeks` | INTEGER |  |
| `ga_days` | INTEGER |  |
| `risk_flags` | JSON | bắt buộc |
| `arrival_hint` | VARCHAR(32) |  |
| `votes` | JSON | bắt buộc |
| `confidence` | FLOAT |  |
| `model` | VARCHAR(64) |  |
| `prompt_version` | VARCHAR(64) |  |
| `rules_version` | VARCHAR(16) |  |
| `masked_note` | TEXT |  |
| `raw` | JSON | bắt buộc |

### `visit_result`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `appointment_id` | INTEGER | PK, FK → appointment.id |
| `data_enc` | TEXT | bắt buộc |
| `source` | VARCHAR(16) | bắt buộc |
| `reference_version` | VARCHAR(16) |  |
| `created_at` | DATETIME | bắt buộc |

### `alert`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `assessment_id` | INTEGER | FK → assessment.id, bắt buộc, index |
| `level` | VARCHAR(8) | bắt buộc |
| `status` | VARCHAR(16) | bắt buộc |
| `created_at` | DATETIME |  |
| `sent_at` | DATETIME |  |
| `send_claimed_at` | DATETIME |  |
| `version` | INTEGER |  |
| `handled_by` | INTEGER |  |
| `handled_at` | DATETIME |  |
| `note` | TEXT |  |
| `final_level` | VARCHAR(16) |  |

### `alert_review`

| Cột | Kiểu | Khóa / ràng buộc |
|---|---|---|
| `id` | INTEGER | PK |
| `alert_id` | INTEGER | FK → alert.id, bắt buộc, index |
| `kind` | VARCHAR(24) | bắt buộc |
| `status` | VARCHAR(16) | bắt buộc |
| `decision` | VARCHAR(32) |  |
| `outcome` | VARCHAR(16) |  |
| `note` | TEXT |  |
| `created_at` | DATETIME | bắt buộc |
| `reviewed_by` | INTEGER |  |
| `reviewed_at` | DATETIME |  |

## 3. Kết quả AI đọc ghi chú (JSON)

Xem `schema/ai_note_output.schema.json`. AI chỉ được chọn mã trong danh mục (enum); số phút đến trước phải kèm mã nguồn đã kiểm (`minutes_basis`).

## 4. Danh mục tri thức khám (config/visit_services.yaml ↔ knowledge/danh_muc_kham.xlsx)

Phiên bản 1.4.0. Gồm:

- `services` (20): mã dịch vụ → tên, từ khóa, số phút dự phòng, tối thiểu có nguồn, giới hạn an toàn, nhịn ăn, nguồn
- `visit_types` (11): loại khám → từ khóa, dịch vụ thường làm, nguồn
- `abbreviations` (31): viết tắt → mã, trạng thái
- `ga_milestones` (7): khoảng tuần thai → dịch vụ
- `sources` (65): nguồn (cấp A/B/C/X, đã kiểm trang gốc hay chưa)
- `facts` (45): câu trích nguyên văn từ nguồn – AI chỉ dùng các câu này
- `visit_flow`: giờ hẹn = giờ gặp bác sĩ; dịch vụ làm trước; thời gian tiếp đón

| Mã dịch vụ | Tên | Tối thiểu có nguồn (phút) |
|---|---|---|
| `KHAM` | Khám bác sĩ |  |
| `SA_2D` | Siêu âm 2D |  |
| `SA_4D` | Siêu âm 4D / hình thái | 15 |
| `SA_DAU_DO` | Siêu âm đầu dò |  |
| `DOPPLER` | Siêu âm Doppler | 15 |
| `NT` | Siêu âm độ mờ da gáy | 15 |
| `CTG` | Monitor tim thai (CTG/NST) | 30 |
| `XN_MAU` | Xét nghiệm máu |  |
| `XN_NUOC_TIEU` | Xét nghiệm nước tiểu |  |
| `OGTT` | Nghiệm pháp dung nạp glucose 75 g | 120 |
| `DOUBLE_TEST` | Double test |  |
| `TRIPLE_TEST` | Triple test |  |
| `NIPT` | NIPT |  |
| `GBS` | Xét nghiệm liên cầu nhóm B (GBS) |  |
| `BHCG` | Beta hCG |  |
| `SOI_DICH` | Soi tươi dịch âm đạo |  |
| `PAP` | Tầm soát ung thư cổ tử cung (Pap/HPV) |  |
| `XN_TIEN_PHAU` | Xét nghiệm tiền phẫu (máu, đông máu, sinh hóa, điện tim, X-quang) |  |
| `KHAM_GAY_ME` | Khám gây mê (tiền mê) |  |
| `THU_THUAT` | Thủ thuật (LEEP, hút buồng, sinh thiết…) |  |

| Mã loại khám | Tên | Dịch vụ thường làm |
|---|---|---|
| `PREOP` | Làm hồ sơ mổ / khám gây mê | KHAM, XN_TIEN_PHAU, KHAM_GAY_ME |
| `PROCEDURE` | Thủ thuật (LEEP, khoét chóp…) | KHAM, XN_MAU, BHCG, THU_THUAT |
| `PRETERM_CHECK` | Nghi chuyển dạ / dọa sinh non | KHAM, SA_2D, CTG |
| `POST_ABORTION` | Tái khám sau hút thai / phá thai nội khoa | KHAM, SA_DAU_DO |
| `POSTPARTUM` | Tái khám sau sinh | KHAM |
| `EARLY_PREG` | Chậm kinh / thai sớm | KHAM, SA_DAU_DO, BHCG |
| `ANC_PACKAGE` | Đăng ký gói thai sản | KHAM, SA_2D, XN_MAU, XN_NUOC_TIEU |
| `ANC` | Khám thai định kỳ | KHAM |
| `GYN` | Khám phụ khoa | KHAM |
| `ULTRASOUND` | Siêu âm đơn thuần | SA_2D |
| `OTHER` | Khác / chưa rõ | KHAM |
