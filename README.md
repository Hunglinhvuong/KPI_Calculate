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

Trước khi thiết kế khóa dữ liệu, phải xác minh ý nghĩa `DATETIME_ID`, tính duy nhất của bản ghi theo ngày và cách nhận diện phường giữa hai nguồn. Không được tự giả định dữ liệu là duy nhất theo ngày nếu nguồn có nhiều bản ghi theo giờ.

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

## Ward-day Playwright Collector

Collector chạy báo cáo `2026_V2 > PHƯỜNG_XÃ > NGÀY`, tải CSV và bỏ ba dòng đầu
trước khi ánh xạ cột, kiểm tra dữ liệu và upsert vào `ward_day`. Tên cột Oracle và
Playwright được định nghĩa trong `data/Map-Ward-Oracle_VS_Playwright.csv`;
`DATETIME_ID` được chuẩn hóa từ cột Playwright `Noc7`. Mặc định lấy ngày hôm qua
và tỉnh `Nghe An`. Cấu hình `SMARTF_USER`/`SMARTF_PASS` (hoặc
`WARD_DAY_USER`/`WARD_DAY_PASSWORD`), `WARD_DAY_URL` nếu cần đổi địa chỉ SmartF,
và PostgreSQL trong `.env` (xem [.env.example](.env.example)). Cài Chromium nếu
máy mới chưa có:

```bash
python -m playwright install chromium

# Chỉ tải/kiểm tra CSV, không ghi PostgreSQL
python -m database.collector_ward_day --dry-run

# Thu thập ngày hôm qua và upsert vào PostgreSQL
python -m database.collector_ward_day

# Chạy lại ngày cụ thể, hiện trình duyệt để gỡ lỗi
python -m database.collector_ward_day --date 2026-10-14 --province "Nghe An" --show
```

```bash
python -m pytest tests/test_collector_ward_day.py -v
```

Upsert dùng khóa `(province, district, datetime_id)` nên có thể chạy lại an toàn.

## VHKT Collector (giai đoạn 4)

Collector chạy độc lập với giao diện và tái sử dụng quy trình SmartF đã kiểm thử
trong `database/smartF_playwright_v3.py`: đăng nhập, mở báo cáo
`2026_V2 > TRẠM > VHKT > NGÀY`, chọn tỉnh/ngày, chạy báo cáo và tải CSV.
Mặc định lấy ngày hôm qua, tỉnh `Nghe An`; CSV được kiểm tra và chuẩn hóa theo
các cột VHKT trước khi upsert vào `vhkt_day`. Chạy lại an toàn theo khóa chống
trùng hiện có trong PostgreSQL.

Thiết lập `VHKT_USER`/`VHKT_PASSWORD` (hoặc `SMARTF_USER`/`SMARTF_PASS`),
`VHKT_URL` nếu dùng địa chỉ SmartF khác mặc định và cấu hình PostgreSQL trong
`.env`. Cài đặt trình duyệt Chromium cho Playwright nếu máy mới chưa có:

```bash
python -m playwright install chromium

# Chỉ tải và kiểm tra CSV, không ghi PostgreSQL
python -m database.collector_vhkt --dry-run

# Tải báo cáo ngày hôm qua và upsert
python -m database.collector_vhkt

# Chạy lại ngày cụ thể, hiện trình duyệt để gỡ lỗi
python -m database.collector_vhkt --date 2026-10-14 --province "Nghe An" --show

# Kiểm thử độc lập, không cần dịch vụ SmartF/PostgreSQL
python -m pytest tests/test_collector_vhkt.py -v
```

## KPI calculation (giai đoạn 5)

Các công thức nằm tập trung trong `KPI/kpi.py`. `calculate_daily_kpi(...)` nhận
DataFrame `ward_day`, `vhkt_day` và danh mục `ward`, trả kết quả theo ngày ở ba
cấp phường, chuyên viên và tỉnh. Ghép phường theo tỉnh/tên phường, bỏ dấu và
không phân biệt hoa thường như quy ước của database; bản ghi không ghép được,
thiếu `number_site` hoặc có nhiều dòng ward_day cho cùng phường/ngày sẽ báo lỗi.

HTMT_KPI và HTMT_QOS tổng hợp theo bình quân gia quyền lưu lượng; MLL_Overdue,
MLL_Ratio dùng tổng số trạm; MLL_Time dùng trung bình `minute_number`.
Mỗi phạm vi cũng trả `site_count` để đối chiếu thủ công và
`TG-MLLTB = (MLL_Overdue_TLHT + MLL_Ratio_TLHT + MLL_Time_TLHT) / 3`.
Ngưỡng VHKT mặc định: Overdue `0.56/0.73`, Ratio `0.043/0.06`, Time
`83.84/109.85` (mục tiêu/tệ nhất); `WO_TLHT=100`. Không có sự cố VHKT trong ngày
thì các số đếm và thời gian bằng 0, tỷ lệ hoàn thành thời gian là 100.

`calculate_monthly_kpi(...)` và `calculate_cumulative_kpi(...)` tính trung bình
cộng KPI ngày, không tính lại tỷ lệ trên tổng số liệu cả kỳ; `site_count` là số
trạm tối đa của phạm vi trong kỳ. Dashboard có biểu đồ đường so sánh MTCL_2026
với TG-MLLTB theo ngày và biểu đồ cột so sánh theo phường/tháng. Kiểm thử:

```bash
python -m pytest tests/test_kpi.py -v
```

## Dashboard và quyền admin (giai đoạn 6)

Chạy giao diện bằng `streamlit run app.py`. Các trang Tổng quan, KPI cá nhân,
Dữ liệu VHKT và Báo cáo tháng đều xem công khai. Chỉ trang Quản trị yêu cầu
đăng nhập để sửa phân công/số trạm hoặc chạy thu thập thủ công. Cấu hình
`ADMIN_USERNAME` và `ADMIN_PASSWORD` trong `.env`; không dùng mật khẩu mặc định
trên môi trường vận hành và không đưa `.env` vào Git.

Hai KPI `MTCL_2026` và `TG-MLLTB` có thang đo riêng, không dùng chung một trục
Y. Tổng quan và KPI cá nhân dùng biểu đồ kết hợp cột MTCL_2026 (trục trái) và
đường TG-MLLTB có điểm (trục phải), theo ngày không hiển thị giờ. Tổng quan thể
hiện mức tỉnh; KPI cá nhân thể hiện mức chuyên viên và có bảng phường lọc theo
ngày, tìm tên và sắp xếp. Dữ liệu VHKT có thể lọc theo chuyên viên. Báo cáo
tháng chọn tháng theo `YYYY-MM`; tháng chưa kết thúc được tính lũy kế từ đầu
tháng đến ngày hiện tại, còn tháng đã kết thúc dùng toàn tháng. Báo cáo có hai
biểu đồ cột độc lập, mỗi biểu đồ so sánh tỉnh và toàn bộ chuyên viên cho một KPI.

## Dọn dữ liệu nguồn hàng tháng (giai đoạn 7)

Tác vụ chỉ chạy vào ngày đầu tháng. Trong tháng N, tác vụ chọn tháng N-3
(ví dụ chạy ngày 01/10/2026 sẽ xử lý dữ liệu 01/07/2026–31/07/2026), tính KPI
tháng theo phường và upsert vào `monthly_summary`, sau đó mới xóa dữ liệu nguồn
tương ứng khỏi `ward_day` và `vhkt_day` (đều lấy từ Playwright). Việc lưu tổng hợp
và xóa dữ liệu nằm trong cùng một transaction; nếu tính toán hoặc lưu tổng hợp
thất bại, dữ liệu nguồn được giữ nguyên. Chạy lại an toàn nhờ khóa
`(month, ward_id)` và thao tác xóa theo khoảng ngày.

Chạy thử không ghi/xóa dữ liệu:

```bash
python -m jobs.jobs cleanup --as-of 2026-10-01 --dry-run
```

Chạy dọn dữ liệu (mặc định lấy ngày trên máy chủ):

```bash
python -m jobs.jobs cleanup
```

Để chạy tự động lúc 02:05 ngày đầu mỗi tháng, thêm vào crontab của tài khoản
được phép truy cập PostgreSQL (đổi đường dẫn theo nơi triển khai):

```cron
5 2 1 * * cd /path/to/KPI-Calculate && ./venv/bin/python -m jobs.jobs cleanup >> /var/log/kpi-monthly-cleanup.log 2>&1
```

Máy chủ cần cấu hình `.env` và múi giờ phù hợp trước khi bật lịch. Tác vụ sẽ
không xóa dữ liệu nếu chạy không đúng ngày đầu tháng hoặc không thể tạo KPI
theo phường cho tháng cần xử lý.

## 10. Tiêu chí hoàn thành

- Cài đặt theo README trên server mới được.
- Collector chạy độc lập với giao diện.
- Có thể chạy lại tác vụ mà không nhân đôi dữ liệu.
- Phân quyền được kiểm tra ở phía truy vấn dữ liệu.
- KPI tháng có kiểm thử.
- Dữ liệu tháng cũ chỉ được xóa sau khi tổng hợp hợp lệ.
- Không để lộ thông tin đăng nhập.
- Không bổ sung kiến trúc phức tạp hoặc phụ thuộc không cần thiết.
