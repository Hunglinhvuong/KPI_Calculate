# DỰ ÁN: DASHBOARD THEO DÕI KPI CHUYÊN VIÊN VIỄN THÔNG — V1

## 1. Mục tiêu

Xây dựng ứng dụng nội bộ bằng Python và Streamlit để theo dõi KPI tháng của chuyên viên quản lý địa bàn phường. Dữ liệu KPI phường và VHKT được thu thập từ SmartF bằng Playwright.

Ưu tiên tối giản, dễ bảo trì, dễ kiểm tra và vận hành trên server nội bộ.

## 2. Công nghệ

- Python.
- Streamlit.
- PostgreSQL.
- Playwright.
- Pandas để xử lý dữ liệu.
- Cron hoặc scheduler bên ngoài để chạy tác vụ định kỳ.

## 3. Cấu trúc dự án

- `app.py`: ứng dụng Streamlit và điều hướng.
- `database/db.py`: kết nối và truy vấn PostgreSQL.
- `database/collector_ward_day.py`: lấy dữ liệu KPI phường từ SmartF bằng Playwright.
- `database/collector_vhkt.py`: lấy dữ liệu VHKT bằng Playwright.
- `KPI/kpi.py`: tính KPI ngày/tháng và tổng hợp kết quả.
- `manage/auth.py`: đăng nhập và phân quyền.
- `manage/admin.py`: quản lý phường, phân công và tác vụ quản trị.
- `jobs/jobs.py`: điểm chạy các tác vụ định kỳ.
- `database/schema.sql`: tạo database.
- `data/`: CSV mẫu và dữ liệu kiểm thử.
- `.env`: cấu hình bí mật, không commit lên Git.
- `requirements.txt`: thư viện.
- `README.md`: hướng dẫn cài đặt, cấu hình, chạy và khắc phục lỗi.

Không tạo thêm module hoặc bảng nếu chưa có nhu cầu rõ ràng.

## 4. Database

Chỉ sử dụng 4 bảng chính:

1. `ward`: mã phường, tỉnh, tên phường, mã chuyên viên, tên chuyên viên, trạng thái hoạt động -> lấy nguồn từ file ward.csv
2. `ward_day`: dữ liệu KPI phường từ SmartF/Playwright, khóa theo phường và kỳ dữ liệu.
3. `vhkt_day`: dữ liệu VHKT từ Playwright, có cơ chế chống ghi trùng (Đã có sẵn code playwright test thành công)
4. `monthly_summary`: kết quả KPI theo tháng và phường, lưu lịch sử các tháng.


`ward_day` giữ các trường theo `WARD_DAY_template.csv`; tên cột Playwright được ánh xạ
theo `data/Map-Ward-Oracle_VS_Playwright.csv`:
`STT, DATETIME_ID, PROVINCE, DISTRICT, MTCL_2026, HTMT_KPI, HTMT_QOS, HTMT_VHKT, SUM(TRAFFIC_ALL_GB), CSSR, PASR, CDR, PADR, HOSR, PRB_DL, DKD, BADLINK_4G, VN_CSSR, VN_CALL_DROP, MC_STAON_NUM_R, USR_DL_THP_4G, USR_DL_THP_5G, RSRP`.

Dữ liệu VHKT giữ các trường:
`PROVINCE_NAME, WARD_NAME, CELLS7, SDATE, EDATE, MINUTE_NUMBER, NETWORK, MA_PHONG_XL, MA_TO_XL, UU_TIEN, DAT`.

`DATETIME_ID` được chuẩn hóa từ `Noc7` theo dạng `YYYYMMDDHH`. Khóa ward_day là
`(province, district, datetime_id)`. `DISTRICT` = `VHKT.WARD_NAME` = `ward.ward_code`
(kèm tỉnh).

## 5. Chức năng

- Tổng quan KPI lũy kế đến hiện tại, có thể chọn theo khoảng thời gian
- KPI theo chuyên viên và từng phường.
- Tra cứu dữ liệu VHKT.
- Báo cáo và so sánh tháng; xuất CSV/Excel.
- Quản trị phân công phường và chạy tác vụ thủ công.

## 6. Thu thập dữ liệu

- Ward-day Playwright Collector và VHKT Collector độc lập.
- Dùng biến môi trường để cấu hình kết nối, không hardcode tài khoản/mật khẩu.
- Có timeout, retry giới hạn, log lỗi và trạng thái thành công/thất bại.
- Chuẩn hóa kiểu dữ liệu trước khi ghi vào PostgreSQL.
- Sử dụng upsert hoặc khóa nguồn ổn định để tránh trùng dữ liệu.
- Tác vụ chạy bằng cron hoặc scheduler ngoài Streamlit.
- Có thể chạy lại một ngày cụ thể để đối soát dữ liệu.
- Có cảnh báo nếu có ngày không lấy được dữ liệu

## 7. KPI và lưu trữ

- Công thức KPI phải tập trung tại `kpi.py`, không rải rác trong giao diện.
- Chưa tự đặt công thức nghiệp vụ khi chưa có định nghĩa từ người dùng.
- Với tỷ lệ, ưu tiên tính từ tổng tử số và mẫu số nếu có dữ liệu nguồn phù hợp.
- Lưu kết quả tổng hợp theo tháng vào `monthly_summary`.
- Tính toán và dọn dữ liệu phải có thể chạy lại an toàn.

## 8. Giao diện

Dùng 5 trang: Tổng quan, KPI cá nhân, Dữ liệu VHKT, Báo cáo tháng, Quản trị.

Giao diện đơn giản, có bộ lọc tháng, phường và chuyên viên; bảng dễ đọc, có trạng thái dữ liệu thiếu và thời điểm cập nhật gần nhất.

## 9. Cách triển khai

Thực hiện từng giai đoạn, không viết toàn bộ ứng dụng trong một lần:

1. Tạo cấu trúc dự án, `schema.sql`, cấu hình môi trường và dữ liệu mẫu.
2. Hoàn thiện kết nối PostgreSQL, nhập thử dữ liệu CSV.
3. Xây dựng Playwright Collector cho ward_day và kiểm thử độc lập.
4. Xây dựng Playwright Collector cho VHKT và kiểm thử độc lập.
5. Xây dựng `kpi.py` và kiểm thử bằng dữ liệu mẫu.
6. Xây dựng đăng nhập, phân quyền và giao diện.
7. Xây dựng `jobs.py`, lịch chạy, đối soát và dọn dữ liệu.
8. Hoàn thiện README, hướng dẫn cài đặt và kiểm thử toàn bộ.

Sau mỗi giai đoạn, cung cấp danh sách file đã tạo/thay đổi, cách chạy, kết quả kiểm thử và các vấn đề còn tồn tại. Không viết giả dữ liệu vào môi trường vận hành.

## 10. Tiêu chí hoàn thành

- Cài đặt theo README trên server mới được.
- Collector chạy độc lập với giao diện.
- Có thể chạy lại tác vụ mà không nhân đôi dữ liệu.
- Phân quyền được kiểm tra ở phía truy vấn dữ liệu.
- KPI tháng có kiểm thử.
- Dữ liệu tháng cũ chỉ được xóa sau khi tổng hợp hợp lệ.
- Không để lộ thông tin đăng nhập.
- Không bổ sung kiến trúc phức tạp hoặc phụ thuộc không cần thiết.
