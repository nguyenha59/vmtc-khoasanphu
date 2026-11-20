# 11 – Nguồn chính thống: máy xét nghiệm, chuẩn dữ liệu, pháp lý, y khoa

Tra cứu ngày **12/11/2026**, phục vụ hướng "AI đọc hồ sơ để chọn câu hỏi phù hợp" (xem `docs/IDEAS.md`).
Mức kiểm chứng: **[gốc]** = trang/tài liệu của hãng, cơ quan ban hành, tạp chí; **[thứ cấp]** = bài tóm tắt, trang tổng hợp
pháp luật, công ty luật – phải đối chiếu văn bản gốc trước khi dùng làm căn cứ.

---

## A. Máy xét nghiệm của khoa (khoa cung cấp 12/11/2026)

| Máy | Nhóm xét nghiệm | Ghi chú từ nguồn | Nguồn |
|---|---|---|---|
| Roche cobas (khoa gọi "cobas 8000 pro") | Miễn dịch tự động: HIV Ag/Ab, vi sinh miễn dịch | Xét nghiệm **Elecsys HIV Duo** (kháng nguyên p24 + kháng thể HIV-1/2) chạy trên mô-đun **cobas e 801 / e 402**; nguồn không nêu "cobas 8000 pro" → **khoa xác nhận tên mô-đun**. Kết quả chính do máy tính tự động từ 2 kết quả phụ. | [gốc] [Roche – Elecsys HIV Duo](https://diagnostics.roche.com/us/en/products/params/elecsys-hiv-duo.html), [FDA – Elecsys HIV Duo](https://www.fda.gov/vaccines-blood-biologics/elecsys-hiv-duo) |
| Beckman Coulter AU5800 | Hóa sinh: ure, creatinin, glucose, AST/ALT… | Không tìm được tài liệu giao tiếp LIS chính thức của AU5800 → **xin hãng / kỹ sư** tài liệu "Host interface specification". | [thứ cấp] [SelectScience – AU5800](https://zephr.selectscience.net/product/au5800-clinical-chemistry-analyzers) |
| Beckman Coulter UniCel DxI 800 | Miễn dịch: ferritin, β-hCG… | Ferritin Access được FDA cho phép dùng hỗ trợ chẩn đoán thiếu/thừa sắt. Giao tiếp LIS theo **ASTM E1394/E1381**, cổng nối tiếp EIA-232 (tài liệu DxI/Access 2 LIS Vendor Information). | [gốc] [FDA K190298 (Access Ferritin)](https://www.accessdata.fda.gov/cdrh_docs/pdf19/K190298.pdf), [Beckman – DxI](https://www.beckmancoulter.com/clinical-studies-and-trends/clinical-studies/automated-immunoassay-analyzer-verification) |
| Siemens CLINITEK (Advantus / Novus / Status+) | Tổng phân tích nước tiểu | Đo: bilirubin, glucose, ceton, **bạch cầu, nitrit**, pH, **protein**, tỉ trọng, urobilinogen; có albumin/creatinin (ACR). **Khoa xác nhận đúng model** (khoa ghi "Clinitek-2"). | [gốc] [Siemens – CLINITEK Novus](https://www.siemens-healthineers.com/en-uk/urinalysis/systems/clinitek-novus), [Siemens – CLINITEK Status+](https://siemens-healthineers.com/urinalysis-products/urinalysis-systems/clinitek-status-plus-self-test) |
| Tosoh HLC-723 G11 | **HbA1c** (HPLC) – khoa ghi "Huyết học" | Máy đo HbA1c; có chế độ biến thể huyết sắc tố và β-thalassemia (HbF, HbA2). | [gốc] [Tosoh – HLC-723G11](https://www.tosohbioscience.com/IN-EN-diagnostics/hplc/analyzer/hlc-723g11) |
| Beckman Coulter DxH 900 | Công thức máu (Hb, tiểu cầu, bạch cầu…) | Thông số theo FDA: WBC, RBC, **HGB**, HCT, MCV, MCH, MCHC, RDW, **PLT**, MPV, công thức bạch cầu 5 thành phần, NRBC. | [gốc] [Beckman – DxH 900](https://www.beckmancoulter.com/products/hematology/dxh-900), [FDA K240252](https://www.accessdata.fda.gov/cdrh_docs/reviews/K240252.pdf) |
| Stago STA R Max | Đông máu | Máy đông máu tự động (đo đông, sắc ký màu, miễn dịch đo độ đục). | [gốc] [FDA K212183 (STA R Max 3)](https://fda.innolitics.com/device/K212183), [thứ cấp] [SelectScience – STA R Max](https://www.selectscience.net/product/sta-r-max) |

**Kết luận cho dự án:** mọi máy đều xuất **kết quả số + đơn vị + khoảng tham chiếu + cờ** sang LIS → lấy dữ liệu số từ LIS/HIS,
**không cần đọc ảnh (computer vision)**.

## B. Chuẩn kết nối và mã hóa dữ liệu xét nghiệm

| Chuẩn | Dùng cho | Nguồn |
|---|---|---|
| **CLSI LIS1-A / LIS2-A2** (trước là ASTM E1381 / E1394) | Máy xét nghiệm ↔ LIS (chuẩn "ASTM" của các máy trên) | [gốc] [ANSI webstore – CLSI LIS2-A2](https://webstore.ansi.org/standards/clsi/lis02a2), [thứ cấp] [ADLM CLN – Lab connectivity](https://myadlm.org/cln/Articles/2012/september/Lab-Connectivity) |
| **HL7 v2 ORU^R01** | LIS → HIS/ứng dụng: gửi kết quả (OBR = nhóm, OBX = từng chỉ số) | [gốc] [HL7 EU – ORU^R01](https://hl7.eu/refactored/msgORU_R01.html) |
| **HL7 FHIR R4 DiagnosticReport + Observation** | Kết quả xét nghiệm dạng API hiện đại (ORU^R01 → DiagnosticReport; OBX → Observation) | [gốc] [SNOMED/LOINC IG – FHIR & lab data](https://docs.snomed.org/implementation-guides/loinc-implementation-guide/information-models-and-terminology-binding/5.3-hl7-fhir-and-laboratory-data), [NHS Care Connect – diagnostics](https://nhsconnect.github.io/CareConnectAPI/build_pcc_diagnostics.html) |
| **LOINC** (Regenstrief) | Mã chuẩn cho từng xét nghiệm (miễn phí) – nên ánh xạ mã nội bộ LIS → LOINC | [gốc] [PubMed – LOINC 5-year update](https://pubmed.ncbi.nlm.nih.gov/12651816/), [HL7 US Core – lab test codes](https://hl7.org/fhir/us/core/STU6/ValueSet-us-core-laboratory-test-codes.html) |
| **VN Core Profile (FHIR R4)** | Đặc tả FHIR cho Việt Nam – **bản lấy ý kiến**, chưa thấy văn bản BYT bắt buộc FHIR | [gốc – dự thảo] [Sở Y tế Hà Tĩnh – VNCoreProfileSpecification v1.4](https://soyte.hatinh.gov.vn/upload/1000030/20210616/20210531_VNCoreProfileSpecification_v1_4_49677f933c.pdf) |

## C. Siêu âm sản khoa

| Nội dung | Nguồn |
|---|---|
| **DICOM SR – TID 5000 "OB-GYN Ultrasound Procedure Report"**: báo cáo siêu âm có cấu trúc (sinh trắc thai TID 5005…), máy siêu âm xuất được số đo dạng có cấu trúc. Có bản bổ sung 2025 (đang lấy ý kiến) cho khảo sát hình thái thai. | [gốc] [DICOM PS3.16 – OB-GYN Report Templates](https://dicom.nema.org/medical/dicom/current/output/chtml/part16/sect_OB-GYNReportTemplates.html), [DICOM Sup 249 (public comment)](https://dicom.nema.org/medical/dicom/Supps/PC/sup249_pc_FetalAnatomySurveySRExtensions.pdf) |
| AI đọc **ảnh** siêu âm = phần mềm là trang thiết bị y tế → ngoài phạm vi MVP (xem mục D – NĐ 98/2021). Dự án chỉ dùng **số đo + kết luận chữ** của phiếu siêu âm. | – |

## D. Pháp lý Việt Nam

| Văn bản | Điểm liên quan dự án | Nguồn |
|---|---|---|
| **Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15** (thông qua 26/6/2025, hiệu lực 01/01/2026) | Dữ liệu sức khỏe = **dữ liệu nhạy cảm**; chuyển dữ liệu ra nước ngoài (kể cả dùng **dịch vụ đám mây / API nước ngoài** như OpenAI) phải **đánh giá tác động** trước. | [gốc] [Bản tiếng Anh – thuvienphapluat](https://thuvienphapluat.vn/van-ban/EN/Bo-may-hanh-chinh/Law-91-2025-QH15-Personal-Data-Protection/665440/tieng-anh.aspx), [thứ cấp] [GV Lawyers – legal alert](https://gvlawyers.com.vn/wp-content/uploads/2025/09/VN_Legal-alert-_Law-on-PDP-2025.pdf) |
| **Nghị định 356/2025/NĐ-CP** (31/12/2025, hiệu lực 01/01/2026, thay NĐ 13/2023) | Hướng dẫn luật trên; mở rộng danh mục dữ liệu nhạy cảm (Điều 3); siết điều kiện chuyển dữ liệu ra nước ngoài (Điều 18). | [gốc] [vbpl.moj.gov.vn – NĐ 356/2025](https://vbpl.moj.gov.vn/TW/Lists/vbpq/Attachments/187276/ND.356.2025.doc), [thứ cấp] [EY – legal alert 3/2026](https://www.ey.com/content/dam/ey-unified-site/ey-com/vi-vn/technical/tax/documents/ey-vietnam-legal-alert-march-2026-decree-no356-2025-nd-cp-providing-detailed-guidance-for-implementation-of-personal-data-protection-law-viet.pdf) |
| **Nghị định 330/2026/NĐ-CP** (19/8/2026) | Xử phạt vi phạm bảo vệ dữ liệu cá nhân. | [thứ cấp] [EY – Decree 330](https://www.ey.com/content/dam/ey-unified-site/ey-com/vi-vn/technical/tax/documents/ey-vn-decree-330-providing-sanctions-for-personal-data-protection.pdf) |
| **Luật Phòng, chống HIV/AIDS** (sửa đổi bởi **Luật 71/2020/QH14**, hiệu lực 01/7/2021) | Bảo mật kết quả xét nghiệm HIV; luật quy định cụ thể **ai được thông báo** kết quả dương tính (Điều 30 sửa đổi). → kết quả HIV **không** đưa cho AI, không lộ qua form/SMS, chỉ người có quyền xem. | [gốc] [Bản tiếng Anh Luật 71/2020](https://thuvienphapluat.vn/van-ban/EN/The-thao-Y-te/Law-71-2020-QH14-amendments-to-the-Law-on-prevention-and-control-of-HIV-AIDS/467784/tieng-anh.aspx), [VBHN 33/VBHN-VPQH 2020](https://static.luatvietnam.vn/tai-file-van-ban-hop-nhat-33-vbhn-vpqh-200744-d5/uploaded/VIETLAWFILE/2021/4/33_VBHN-VPQH_2020_070421085916.pdf) |
| **Luật Khám bệnh, chữa bệnh 15/2023/QH15** (hiệu lực 01/01/2024; VBHN 26/VBHN-VPQH 2026) | Người bệnh được giữ bí mật thông tin trong hồ sơ bệnh án; hồ sơ lưu trữ bảo mật, chỉ người được phép khai thác. | [gốc] [thuvienphapluat – Luật 15/2023](https://thuvienphapluat.vn/van-ban/The-thao-Y-te/Luat-15-2023-QH15-kham-benh-chua-benh-372143.aspx), [VBHN 26/2026](https://thuvienphapluat.vn/van-ban/The-thao-Y-te/Van-ban-hop-nhat-26-VBHN-VPQH-2026-Luat-Kham-benh-chua-benh-696163.aspx) |
| **Nghị định 102/2025/NĐ-CP** quản lý dữ liệu y tế (13/5/2025, hiệu lực 01/7/2025) | Văn bản đầu tiên quy định toàn diện xây dựng, kết nối, **chia sẻ dữ liệu y tế**; Điều 24 về quyền khai thác dữ liệu. Cần đọc trước khi nối MyVinmec / HIS. | [thứ cấp] [LuatVietnam – NĐ 102/2025](https://luatvietnam.vn/y-te/nghi-dinh-102-2025-nd-cp-cua-chinh-phu-quy-dinh-quan-ly-du-lieu-y-te-400071-d1.html), [Điều 24](https://hethongphapluat.com/nghi-dinh-102-2025-nd-cp-quy-dinh-quan-ly-du-lieu-y-te/dieu-24) |
| **Thông tư 13/2025/TT-BYT** hồ sơ bệnh án điện tử (06/6/2025, hiệu lực 21/7/2025) | **Thay Thông tư 46/2018/TT-BYT**; hạn triển khai bệnh án điện tử chậm nhất 31/12/2026. | [thứ cấp] [caselaw.vn – TT 13/2025](https://caselaw.vn/van-ban-phap-luat/587211-thong-tu-so-13-2025-tt-byt-ngay-06-06-2025-cua-bo-truong-bo-y-te-huong-dan-trien-khai-ho-so-benh-an-dien-tu) |
| **Nghị định 98/2021/NĐ-CP** quản lý trang thiết bị y tế (sửa bởi **NĐ 07/2023**; VBHN 03/VBHN-BYT 2023) | **Phần mềm có thể là trang thiết bị y tế** (phân loại A–D theo rủi ro). Phần mềm tự đưa ra chẩn đoán / đọc ảnh y khoa có nguy cơ thuộc diện phải phân loại, công bố → giữ app ở vai trò **hỗ trợ, người quyết định**. | [gốc] [thuvienphapluat – NĐ 98/2021](https://thuvienphapluat.vn/van-ban/The-thao-Y-te/Nghi-dinh-98-2021-ND-CP-quan-ly-trang-thiet-bi-y-te-493940.aspx), [NĐ 07/2023](https://thuvienphapluat.vn/van-ban/The-thao-Y-te/Nghi-dinh-07-2023-ND-CP-sua-doi-Nghi-dinh-98-2021-ND-CP-quan-ly-trang-thiet-bi-y-te-557403.aspx?tab=1) |

## E. Y khoa (bổ sung `knowledge/sources/y-khoa.md`)

| Nội dung | Điểm chính | Nguồn |
|---|---|---|
| **QĐ 1470/QĐ-BYT** (29/5/2024) – Hướng dẫn quốc gia sàng lọc và quản lý ĐTĐ thai kỳ | Thay **QĐ 6173/QĐ-BYT (2018)**. Là căn cứ hiện hành cho ngưỡng đường huyết thai kỳ trong `rules.yaml`. | [gốc] [Toàn văn (BV Tâm Anh lưu)](https://tamanhhospital.vn/wp-content/uploads/2026/09/huong-dan-quoc-gia-ve-sang-loc-va-quan-ly-dai-thao-duong-thai-ky.pdf), [thứ cấp] [TT CSSKSS Khánh Hòa](https://ttcsskss.khanhhoa.gov.vn/Portals/0/DAI%20THAO%20DUNG%20THAI%20K%20%20Trung%20tam%20CSSKSS%20KH.pdf) |
| **IADPSG 2010** – tiêu chuẩn chẩn đoán ĐTĐ thai kỳ | Nghiệm pháp 75 g lúc 24–28 tuần; chẩn đoán khi ≥ 1 giá trị: đói **≥ 5,1**, 1 giờ **≥ 10,0**, 2 giờ **≥ 8,5** mmol/L. (Khác ngưỡng **theo dõi** 5,3 / 7,8 / 6,7 đang dùng cho module M-ĐTĐ.) | [gốc] Diabetes Care 2010;33(3):676–82, doi:10.2337/dc09-1848; [PMC – bảng tiêu chuẩn](https://pmc.ncbi.nlm.nih.gov/articles/PMC2867808/table/T1) |
| **WHO – thiếu máu khi mang thai** | Hb **< 110 g/L** (quý 1 và 3), **< 105 g/L** (quý 2). | [gốc] [WHO ANC 2016](https://www.who.int/publications-detail-redirect/9789241549912), [thứ cấp] [PMC8098957](https://pmc.ncbi.nlm.nih.gov/articles/PMC8098957) |
| **WHO ANC 2016** – chăm sóc trước sinh | 49 khuyến cáo; vi khuẩn niệu không triệu chứng: cấy nước tiểu là chuẩn (que nhúng chỉ khi thiếu điều kiện); xét nghiệm HIV, giang mai thường quy. | [gốc] [WHO – ANC recommendations](https://www.who.int/publications-detail-redirect/9789241549912), [Executive summary WHO/RHR/16.12](https://www.who.int/publications-detail-redirect/WHO-RHR-16.12) |
| **ISSHP 2021** (Magee và cs., Pregnancy Hypertension 2022;27:148–69) | Tăng huyết áp thai kỳ ≥ 140/90, nặng ≥ 160/110; tiền sản giật **không bắt buộc protein niệu** (tăng HA + tổn thương cơ quan mẹ / rối loạn bánh rau). Ngưỡng tiểu cầu / men gan: **đọc Bảng 1 bản gốc** (chưa trích được). | [gốc] [Bản thảo tác giả – DUO UiO](https://www.duo.uio.no/bitstream/10852/98656/4/Red-Magee_et_al_2022_manuscript.pdf), doi:10.1016/j.preghy.2021.09.008 |
| **EPDS bản tiếng Việt** – Tran và cs., J Affect Disord 2011;133:281–93 (PMID 21529963) | Phụ nữ chu sinh miền Bắc: điểm cắt tối ưu **3/4** (độ nhạy 69,7%, độ đặc hiệu 72,9%) – thấp hơn nhiều nơi khác. Một số nghiên cứu sau vẫn dùng **12/13**. | [gốc] [PubMed 21529963](https://www.curehunter.com/public/pubmed21529963.do), [Tran và cs. BMC Psychiatry 2013](https://bmcpsychiatry.biomedcentral.com/track/pdf/10.1186/1471-244X-13-24) |

## F. MyVinmec

| Nội dung | Nguồn |
|---|---|
| Ứng dụng có đặt lịch, nhắc lịch, kết nối hồ sơ y tế Vinmec, xem **hồ sơ khám sức khỏe tổng quát**; nguồn công khai **không** mô tả API cho bên thứ ba hay việc xem từng kết quả xét nghiệm lẻ → tích hợp phải qua bộ phận CNTT Vinmec. | [gốc] [Vinmec – MyVinmec](https://www.vinmec.com/vie/bai-viet/my-vinmec-ung-dung-dien-thoai-giup-quan-ly-tron-ven-suc-khoe-ca-nhan-va-gia-dinh-vi), [Hướng dẫn xem kết quả KSK](https://www.vinmec.com/vie/bai-viet/huong-dan-theo-doi-ket-qua-kham-suc-khoe-tong-quat-tai-ung-dung-myvinmec-vi) |


## F2. Chẩn đoán của bác sĩ (mã bệnh ICD-10)

| Văn bản / chuẩn | Điểm liên quan dự án | Nguồn |
|---|---|---|
| **Thông tư 06/2026/TT-BYT** (02/4/2026) – mã hóa bệnh tật, nguyên nhân tử vong theo **ICD-10** | Văn bản **mới nhất**: ban hành bản cập nhật ICD-10 dùng **đến khi Việt Nam triển khai ICD-11**; hướng dẫn mã hóa kèm QĐ 4469 hết hiệu lực khi văn bản mới có hiệu lực. | [gốc] [Cổng Chính phủ – TT 06/2026](https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/4/06-byt.pdf), [thứ cấp] [LuatVietnam](https://luatvietnam.vn/y-te/thong-tu-06-2026-tt-byt-quy-dinh-ma-hoa-benh-tat-nguyen-nhan-tu-vong-theo-icd-10-430742-d1.html), [LSVN](https://lsvn.vn/trien-khai-thong-tu-moi-ve-ma-hoa-benh-tat-a173907.html) |
| **QĐ 1849/QĐ-BYT** (2026) – hướng dẫn kỹ thuật mã hóa ICD-10 | Quy tắc chọn mã bệnh chính / kèm theo. | [thứ cấp] [LuatVietnam – QĐ 1849/2026](https://luatvietnam.vn/y-te/quyet-dinh-1849-qd-byt-2026-huong-dan-ky-thuat-ma-hoa-benh-tat-va-nguyen-nhan-tu-vong-theo-icd-10-438487-d1.html), [Sở YT Đồng Tháp – nguyên tắc mã hóa](https://dongthap.gov.vn//documents/1589390/8249360/260408_PL2_Huong_dan_nguyen_tac_ma_hoa_ICD10_1776931811259.pdf/621d6bf4-70ea-d5dc-82c8-effdcf427e5f) |
| QĐ 4469/QĐ-BYT (28/10/2020) + QĐ 4400/QĐ-BYT (23/10/2020) | ICD-10 bản Việt Nam (nền WHO 2019); 4400 áp dụng cho cơ sở KCB, thay danh mục QĐ 7603/2018. | [gốc] [QĐ 4469 (PDF)](https://static.luatvietnam.vn/tai-file-quyet-dinh-4469-phan-loai-quoc-te-ma-hoa-benh-tat-nguyen-nhan-tu-vong-icd-10-193069-d1/uploaded/VIETLAWFILE/2020/10/4469_QD-BYT_281020170630_unzip/4469_qd_byt.pdf), [QĐ 4400 (Sở YT Đồng Nai)](https://syt.dongnai.gov.vn/Lists/Vn%20bn/Attachments/4688/4400.signed.pdf) |
| **QĐ 130/QĐ-BYT** (18/01/2023), sửa bởi **QĐ 4750/QĐ-BYT** (29/12/2023) và **QĐ 3176/QĐ-BYT** (29/10/2024) | Chuẩn dữ liệu đầu ra KCB/BHYT (XML) – HIS **đã có sẵn mã bệnh ICD-10** (bệnh chính, bệnh kèm) ở định dạng này → nguồn chẩn đoán có cấu trúc. "XML 4210" chỉ là tên gọi cũ (QĐ 4210 hết hiệu lực 01/7/2024). | [gốc] [QĐ 130 (PDF)](https://static.luatvietnam.vn/tai-file-quyet-dinh-130-qd-byt-241607-d1/uploaded/VIETLAWFILE/2023/1/130_QD-BYT_270123205700.pdf.aspx), [thứ cấp] [HL7 VN – FHIR & BHYT](https://hl7-org-vn.pages.dev/en/knowledge/fhir-bhyt/) |
| **VN Core FHIR – CodeSystem ICD-10 Việt Nam** | Bộ mã ICD-10 VN dạng FHIR (tham khảo kỹ thuật, không phải văn bản pháp lý). | [thứ cấp] [VN Core IG – vn-icd10](https://vncore-ig.pages.dev/CodeSystem-vn-icd10-cs.html) |
| ICD-10 **Chương XV (O00–O99)**: thai nghén, sinh đẻ, hậu sản | Mã hay gặp: **O13** tăng HA thai kỳ, **O14** tiền sản giật, **O15** sản giật, **O24.4** ĐTĐ thai kỳ, **O44** rau tiền đạo, O60 dọa đẻ non… Nguồn tìm được là bản **ICD-10-CM (Mỹ)** – mã con khác bản WHO/VN → **đối chiếu bản ICD-10 Việt Nam**. | [gốc – bản Mỹ] [CMS ICD-10 manual](https://www.cms.gov/icd10m/FY2027-fr-v44-fullcode-cms/fullcode_cms/P1570.html); bản WHO: [icd.who.int](https://icd.who.int/browse10) |
| **QĐ 315/QĐ-BYT** (29/01/2015) – Hướng dẫn chẩn đoán và điều trị các bệnh sản phụ khoa | Tài liệu chuyên môn gốc của BYT (chủ biên PGS.TS Nguyễn Viết Tiến); **chưa xác nhận còn hiệu lực** – BYT ban hành thêm nhiều hướng dẫn sản khoa năm 2024 (tiền sản giật, ĐTĐ thai kỳ). | [thứ cấp] [caselaw – QĐ 315/2015](https://caselaw.vn/van-ban-phap-luat/157786-quyet-dinh-315-qd-byt-nam-2015-ve-tai-lieu-chuyen-mon-huong-dan-chan-doan-va-dieu-tri-cac-benh-san-phu-khoa-do-bo-truong-bo-y-te-ban-hanh), [Sở YT Khánh Hòa – các hướng dẫn chuyên môn BYT 2024](https://syt.khanhhoa.gov.vn/Resources/Docs/000.Nam%202025/CONGVAN/CV%20ph%E1%BB%95%20bi%E1%BA%BFn,%20tri%E1%BB%83n%20khai%20c%C3%A1c%20H%C6%B0%E1%BB%9Bng%20d%E1%BA%ABn%20chuy%C3%AAn%20m%C3%B4n%20%C4%91%C3%A3%20%C4%91%C6%B0%E1%BB%A3c%20B%E1%BB%99%20Y%20t%E1%BA%BF%20ban%20h%C3%A0nh%20trong%20n%C4%83m%202024.signed.signed.signed.pdf) |
| ICD-11 | WHO áp dụng từ 01/01/2022; **Việt Nam chưa có lộ trình chính thức** – thiết kế nên để bảng mã cấu hình được. | [gốc] [WHO Việt Nam – ICD-11](https://www.who.int/vietnam/news/fact-sheets/detail/icd-11) |

**Cách dùng trong dự án:** chẩn đoán đã mã hóa ICD-10 là dữ liệu **có cấu trúc** → **quy tắc** đọc (ví dụ O14 → nhóm câu hỏi tiền sản giật,
O24.4 → nhóm đường huyết), không cần AI. AI chỉ đọc phần **chẩn đoán ghi bằng chữ** chưa có mã. Mã B20–B24 (HIV) và chẩn đoán
nhạy cảm khác → xử lý như mục G.2 (không đưa cho AI, không lộ qua form).

---

## G. Hệ quả cho dự án (cần khoa / bác sĩ / pháp chế quyết)

1. **EPDS:** `rules.yaml` đang dùng ngưỡng **13**; bản tiếng Việt đã kiểm định (2011) cho điểm cắt **3/4**. Ngoài ra bộ câu
   trong `config/forms.yaml` là **bản diễn đạt lại**, không phải bản kiểm định → bác sĩ chọn: dùng nguyên bộ câu + điểm cắt
   của Tran 2011, hoặc giữ bản hiện tại và tự kiểm định.
2. **HIV (và viêm gan B, giang mai):** theo Luật 71/2020 – không đưa cho AI (kể cả cục bộ), không lộ gián tiếp qua câu hỏi
   form gửi SMS, phân quyền xem riêng.
3. **OpenAI / API nước ngoài với dữ liệu thật:** Luật 91/2025 + NĐ 356/2025 → cần đồng ý của người bệnh và hồ sơ đánh giá
   tác động chuyển dữ liệu ra nước ngoài. Rào `SYNTHETIC_DATA_ONLY` + chặn production giữ nguyên đến khi pháp chế duyệt.
4. **Phần mềm y tế (NĐ 98/2021):** giữ AI ở vai trò gợi ý, người quyết định; không đọc ảnh siêu âm; không tự chẩn đoán.
5. **Tích hợp dữ liệu:** xin khoa/CNTT (a) file xuất mẫu từ LIS hoặc đặc tả HL7/ASTM của LIS, (b) danh mục mã xét nghiệm nội bộ
   để ánh xạ LOINC, (c) xác nhận tên mô-đun Roche (e 801?) và model CLINITEK.
6. **Văn bản đã thay thế:** dùng **TT 13/2025** (không còn TT 46/2018); **QĐ 1470/2024** (không còn QĐ 6173/2018);
   mã bệnh theo **TT 06/2026** (không còn hướng dẫn kèm QĐ 4469/2020); dữ liệu đầu ra theo **QĐ 130/2023** (không còn QĐ 4210/2017).
8. **Chẩn đoán:** xin CNTT cho biết HIS xuất mã ICD-10 (bệnh chính / bệnh kèm) ở đâu – thường có sẵn theo chuẩn QĐ 130.
7. **Đọc bản gốc trước khi chốt ngưỡng:** ISSHP 2021 Bảng 1 (tiểu cầu, men gan, creatinin), QĐ 1470/2024 (ngưỡng theo dõi
   đường huyết), NĐ 102/2025 (điều kiện chia sẻ dữ liệu y tế).

## H. Dữ liệu công khai trên mạng – dùng được gì (12/11/2026)

| Nguồn | Là gì | Dùng cho dự án? | Nguồn |
|---|---|---|---|
| UCI Maternal Health Risk (ID 863) | 1.013 thai phụ nông thôn Bangladesh; 6 chỉ số (tuổi, HA, đường huyết, nhiệt độ, nhịp tim) → mức nguy cơ thấp/vừa/cao; CC BY 4.0 | **Không** đưa vào app: khác dân số, quá nhỏ, không rõ ai gán nhãn; mức cảnh báo của dự án do bác sĩ duyệt. Chỉ để thử kỹ thuật. | [gốc] [UCI – Maternal Health Risk](https://archive.ics.uci.edu/dataset/863/maternal+health+risk) |
| MIMIC-III/IV (PhysioNet) | Hồ sơ hồi sức tích cực Mỹ; có bộ phụ "yếu tố xã hội – kết cục thai kỳ" | **Không**: cần chứng chỉ + DUA, cấm chia sẻ lại, dân số hồi sức Mỹ. | [gốc] [PhysioNet – SDOH adverse pregnancy](https://www.physionet.org/content/sdoh-adverse-pregnancy/), [MIMIC-IV](https://physionet.org/content/mimiciv/) |
| **ViHealthBERT** (LREC 2022) | Mô hình ngôn ngữ tiền huấn luyện trên **văn bản y tế tiếng Việt** (2 bản base ~135M tham số) | **Có**: nền cho lớp hiểu nghĩa câu thai phụ viết (chạy cục bộ). Kiểm tra giấy phép trước khi dùng. | [gốc] [ACL Anthology – LREC 2022](https://preview.aclanthology.org/setup/2022.lrec-1.35), [Hugging Face – vihealthbert-base-syllable](https://huggingface.co/demdecuong/vihealthbert-base-syllable) |
| **ViMQ** | Câu hỏi y khoa của bệnh nhân Việt, gán nhãn ý định + thực thể (triệu chứng…) | **Có thể**: thêm dữ liệu cách nói khẩu ngữ để kiểm thử / tinh chỉnh lớp hiểu nghĩa. Kiểm tra giấy phép. | [gốc] [GitHub tadeephuy/ViMQ (qua bài báo ViHealthBERT)](https://preview.aclanthology.org/setup/2022.lrec-1.35) |
| Hướng dẫn WHO / BYT / ICD (mục D–F2, `knowledge/sources/y-khoa.md`) | Tri thức y khoa chính thống | **Có**, dạng **tra cứu có trích dẫn** (RAG) và làm căn cứ ngân hàng câu hỏi / danh mục – không "huấn luyện". | – |
