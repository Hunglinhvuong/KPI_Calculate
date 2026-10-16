# KPI mức tỉnh, mức chuyên viên được tính từ kpi mức xã theo ngày. Nguyên tắc tính là tổng hợp từ các xã do chuyên viên đó phụ trách (với mức chuyên viên) hoặc tính toàn bộ (với mức tỉnh). Nhắc lại liên kết từ các bảng ward.ward_code <-> vhkt_day.ward_name, ward.ward_code <-> ward_day.district
# KPI mức lũy kế và mức tháng được tính từ trung bình cộng mức ngày
# MTCL_2026 = (0.2* HTMT_QoS + 0.4* HTMT_KPI + 0.4* HTMT_VHKT) - HTMT_ATM, giá trị không âm
## Công thức HTMT_KPI
	HTMT_KPI = SUM(ward_day.traffic_all_gb*ward_day.htmt_kpi)/SUM(ward_day.traffic_all_gb)
## Công thức HTMT_QoS
	HTMT_QoS = SUM(ward_day.traffic_all_gb*ward_day.htmt_qos)/SUM(ward_day.traffic_all_gb)
## Công thức HTMT_VHKT
	HTMT_VHKT = 0.2*(MLL_Overdue_TLHT+MLL_Ratio_TLHT+MLL_Time_TLHT) + 0.4* WO_TLHT
### Các ngưỡng được định nghĩa sẵn
	- MLL_Overdue_MTCL (mục tiêu)
	- MLL_Overdue_Worst (tệ nhất)
	- MLL_Ratio_MTCL
	- MLL_Ratio_Worst
	- MLL_Time_MTCL
	- MLL_Time_Worst
	- WO_TLHT = 100 (tạm thời để 100)
### Giá trị thực hiện
	MLL_Overdue = SUM(COUNT(vhkt_day.dat = 'N')/SUM(ward.number_site) * 100, nếu vhkt_day không có thì MLL_Overdue = 0
	MLL_Ratio = SUM(COUNT(vhkt_day)/SUM(ward.number_site), nếu vhkt_day không có thì MLL_Ratio = 0
	MLL_Time = AVERAGE(vhkt_day.minute_number)
### Tỷ lệ hoàn thành
	MLL_Overdue_TLHT (Tỷ lệ hoàn thành) = 
		- Nếu MLL_Overdue <= MLL_Overdue_MTCL thì MLL_Overdue_TLHT = 100
		- Nếu MLL_Overdue >= MLL_Overdue_Worst thì MLL_Overdue_TLHT = 0
		- Còn lại, MLL_Overdue_TLHT = 70 + 30*(MLL_Overdue - MLL_Overdue_Worst)/(MLL_Overdue_MTCL-MLL_Overdue_Worst)

	MLL_Ratio_TLHT (Tỷ lệ hoàn thành) = 
		- Nếu MLL_Ratio <= MLL_Ratio_MTCL thì MLL_Ratio_TLHT = 100
		- Nếu MLL_Ratio >= MLL_Ratio_Worst thì MLL_Ratio_TLHT = 0
		- Còn lại, MLL_Ratio_TLHT = 70 + 30*(MLL_Ratio - MLL_Ratio_Worst)/(MLL_Ratio_MTCL-MLL_Ratio_Worst)

	MLL_Time_TLHT (Tỷ lệ hoàn thành) = 
		- Nếu MLL_Time <= MLL_Time_MTCL thì MLL_Time_TLHT = 100
		- Nếu MLL_Time >= MLL_Time_Worst thì MLL_Time_TLHT = 0
		- Còn lại, MLL_Time_TLHT = 70 + 30*(MLL_Time - MLL_Time_Worst)/(MLL_Time_MTCL-MLL_Time_Worst)
## HTMT_ATM = SUM(ward_day.htmt_anm)