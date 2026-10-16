"""Public Streamlit dashboard with an admin-only management page."""
from __future__ import annotations

from datetime import date, timedelta
import logging

import pandas as pd
import streamlit as st

from KPI.kpi import calculate_cumulative_kpi, calculate_daily_kpi, month_to_date_range
from KPI.charts import daily_kpi_combo_chart, monthly_kpi_comparison_chart
from database import db
from manage import admin, auth


log = logging.getLogger(__name__)
PAGE_NAMES = [
    "Tổng quan",
    "KPI cá nhân",
    "Dữ liệu VHKT",
    "Báo cáo tháng",
    "Quản trị",
]
KPI_DISPLAY_COLUMNS = {
    "htmt_kpi": "HTMT KPI",
    "htmt_qos": "HTMT QoS",
    "ml_overdue": "Over_due",
    "ml_overdue_tlht": "TLHT Over_due",
    "ml_ratio": "Ratio MLL",
    "ml_ratio_tlht": "TLHT Ration MLL",
    "ml_time": "thời gian MLL (phút)",
    "ml_time_tlht": "TLHT thời gian MLL",
    "tg_mllltb": "TLHT TG-MLLTB",
    "wo_tlht": "WO TLHT",
    "htmt_vhkt": "HTMT VHKT",
    "htmt_anm": "HTMT ANM",
    "mtcl_2026": "MTCL 2026",
    "traffic_all_gb": "Lưu lượng (GB)",
    "site_count": "Số trạm",
}

st.set_page_config(page_title="Dashboard KPI viễn thông", layout="wide")


def _render_error(exc: Exception) -> None:
    log.exception("Dashboard operation failed", exc_info=exc)
    st.error(f"Không thể tải dữ liệu: {exc}")
    st.info("Kiểm tra PostgreSQL và cấu hình kết nối trong `.env`, sau đó tải lại trang.")


def _date_range(key: str) -> tuple[date, date]:
    default_start = date.today() - timedelta(days=7)
    selected = st.date_input(
        "Khoảng ngày",
        value=(default_start, date.today()),
        key=key,
    )
    if isinstance(selected, tuple) and len(selected) == 2:
        start_date, end_date = selected
        if start_date > end_date:
            st.warning("Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.")
            return end_date, start_date
        return start_date, end_date
    st.caption("Chọn cả ngày bắt đầu và ngày kết thúc để xem dữ liệu.")
    return selected, selected


def _province_filter(wards: pd.DataFrame, key: str) -> str | None:
    provinces = sorted(wards["province_name"].dropna().astype(str).unique().tolist())
    if not provinces:
        st.warning("Chưa có danh mục phường.")
        return None
    return st.selectbox("Tỉnh", ["Tất cả", *provinces], key=key) or "Tất cả"


def _load_kpi_inputs(start_date: date, end_date: date, province: str | None):
    wards = db.get_wards()
    ward_day = db.get_ward_day(start_date, end_date, province)
    if province:
        wards = wards[wards["province_name"].map(db.norm_name) == db.norm_name(province)]
    vhkt_day = db.get_vhkt_day(start_date, end_date, province)
    if ward_day.empty:
        return wards, ward_day, vhkt_day, pd.DataFrame()
    daily = calculate_daily_kpi(ward_day, vhkt_day, wards)
    return wards, ward_day, vhkt_day, daily


def _show_kpi_table(frame: pd.DataFrame, *, scope_type: str | None = None) -> None:
    if frame.empty:
        st.info("Chưa có dữ liệu KPI trong khoảng thời gian đã chọn.")
        return
    view = frame.copy()
    if scope_type and "scope_type" in view:
        view = view[view["scope_type"] == scope_type]
    columns = [column for column in ("data_date", "province_name", "scope_name", "days_with_data", *KPI_DISPLAY_COLUMNS) if column in view]
    labels = {"province_name": "Tỉnh", "scope_name": "Đối tượng", "data_date": "Ngày", "days_with_data": "Số ngày"}
    view = view[columns].rename(columns={**labels, **KPI_DISPLAY_COLUMNS})
    st.dataframe(view, width="stretch", hide_index=True)


def _show_primary_kpi_trend(
    frame: pd.DataFrame,
    title: str,
) -> None:
    if frame.empty or not {"mtcl_2026", "tg_mllltb"}.issubset(frame.columns):
        return
    st.subheader(title)
    st.altair_chart(daily_kpi_combo_chart(frame), width="stretch")


def _show_primary_kpi_comparison(
    frame: pd.DataFrame,
    title: str,
    *,
    label: str = "scope_name",
    metric: str | None = None,
) -> None:
    if metric is None:
        raise ValueError("Cần chỉ định một KPI cho biểu đồ so sánh tháng")
    if frame.empty or label not in frame.columns or metric not in frame:
        return
    st.subheader(title)
    chart_frame = frame.rename(columns={label: "scope_label"})
    st.altair_chart(monthly_kpi_comparison_chart(chart_frame, metric), width="stretch")


def _render_overview(wards: pd.DataFrame) -> None:
    st.header("Tổng quan KPI")
    first, last = _date_range("overview_range")
    province = _province_filter(wards, "overview_province")
    try:
        selected_province = None if province == "Tất cả" else province
        _, _, _, daily = _load_kpi_inputs(first, last, selected_province)
        if daily.empty:
            st.info("Chưa có dữ liệu KPI phường trong khoảng thời gian đã chọn.")
            return
        summary = calculate_cumulative_kpi(daily, first, last)
    except Exception as exc:
        _render_error(exc)
        return

    province_summary = summary[summary["scope_type"] == "province"]
    if province_summary.empty:
        st.info("Không có dữ liệu KPI tỉnh để tổng hợp.")
        return
    if selected_province:
        values = province_summary.iloc[0]
        cards = st.columns(4)
        cards[0].metric("MTCL 2026", _format_metric(values.get("mtcl_2026")))
        cards[1].metric("HTMT KPI", _format_metric(values.get("htmt_kpi")))
        cards[2].metric("HTMT QoS", _format_metric(values.get("htmt_qos")))
        cards[3].metric("HTMT VHKT", _format_metric(values.get("htmt_vhkt")))
        st.caption(f"{selected_province} · bình quân {int(values['days_with_data'])} ngày có dữ liệu.")
    else:
        st.info("Chọn một tỉnh để xem các chỉ số tổng hợp ở đầu trang.")
    st.subheader("Tổng hợp theo tỉnh")
    st.dataframe(
        province_summary.rename(columns={"scope_name": "Tỉnh", "days_with_data": "Số ngày"})
        .drop(columns=["scope_type", "scope_id", "province_name"], errors="ignore")
        .rename(columns=KPI_DISPLAY_COLUMNS),
        width="stretch",
        hide_index=True,
    )
    st.subheader("Diễn biến KPI ngày")
    _show_primary_kpi_trend(
        daily[daily["scope_type"] == "province"],
        "Diễn biến KPI ngày mức tỉnh",
    )
    _show_kpi_table(daily, scope_type="province")


def _render_employee_kpi(wards: pd.DataFrame) -> None:
    st.header("KPI cá nhân")
    first, last = _date_range("employee_range")
    province = _province_filter(wards, "employee_province")
    selected_province = None if province == "Tất cả" else province
    try:
        selected_wards, _, _, daily = _load_kpi_inputs(first, last, selected_province)
        if daily.empty:
            st.info("Chưa có dữ liệu KPI trong khoảng thời gian đã chọn.")
            return
    except Exception as exc:
        _render_error(exc)
        return

    employee_daily = daily[daily["scope_type"] == "employee"]
    employee_daily = employee_daily[employee_daily["scope_id"].notna()]
    if employee_daily.empty:
        st.info("Chưa có phân công chuyên viên cho dữ liệu KPI đang xem.")
        return
    choices = {
        f"{row.scope_name or row.scope_id} ({row.scope_id}) — {row.province_name}": (
            row.scope_id,
            row.province_name,
        )
        for row in employee_daily.drop_duplicates(["scope_id", "province_name"]).itertuples()
    }
    selected_label = st.selectbox("Chuyên viên", list(choices), key="employee_choice")
    employee_code, employee_province = choices[selected_label]
    selected_employee_daily = employee_daily[
        (employee_daily["scope_id"] == employee_code)
        & (employee_daily["province_name"] == employee_province)
    ]
    _show_primary_kpi_trend(
        selected_employee_daily,
        f"KPI ngày của {selected_label}",
    )

    ward_daily = daily[
        (daily["scope_type"] == "ward")
        & daily["scope_id"].isin(
            selected_wards.loc[
                (selected_wards["employee_code"] == employee_code)
                & (selected_wards["province_name"] == employee_province),
                "ward_id",
            ]
        )
        & (daily["province_name"] == employee_province)
    ]
    st.subheader("Chi tiết phường")
    if ward_daily.empty:
        st.info("Chuyên viên chưa có KPI phường trong khoảng thời gian đã chọn.")
        return
    available_dates = sorted(ward_daily["data_date"].unique(), reverse=True)
    selected_date = st.selectbox(
        "Ngày chi tiết",
        available_dates,
        format_func=lambda value: value.strftime("%Y-%m-%d"),
        key="employee_ward_date",
    )
    ward_search = st.text_input("Lọc phường", key="employee_ward_search").strip().casefold()
    ward_details = ward_daily[ward_daily["data_date"] == selected_date].copy()
    if ward_search:
        ward_details = ward_details[
            ward_details["scope_name"].astype(str).str.casefold().str.contains(ward_search, regex=False)
        ]
    ward_details = ward_details.sort_values("mtcl_2026", na_position="last")
    _show_kpi_table(ward_details, scope_type="ward")


def _render_vhkt(wards: pd.DataFrame) -> None:
    st.header("Tra cứu dữ liệu VHKT")
    first, last = _date_range("vhkt_range")
    province = _province_filter(wards, "vhkt_province")
    selected_province = None if province == "Tất cả" else province
    employee_wards = wards
    if selected_province:
        employee_wards = employee_wards[
            employee_wards["province_name"].map(db.norm_name) == db.norm_name(selected_province)
        ]
    employee_options = {}
    assigned_wards = employee_wards[employee_wards["employee_code"].notna()]
    for (code, name, employee_province), group in assigned_wards.groupby(
        ["employee_code", "employee_name", "province_name"], dropna=False
    ):
        display_name = str(name).strip() if pd.notna(name) else str(code)
        employee_options[
            f"{display_name} ({code}) — {employee_province}"
        ] = group["ward_id"].tolist()
    selected_employee = st.selectbox(
        "Chuyên viên",
        ["Tất cả", *sorted(employee_options)],
        key="vhkt_employee",
    )
    ward_search = st.text_input("Lọc tên phường/trạm", key="vhkt_search").strip().casefold()
    try:
        frame = db.get_vhkt_day(first, last, selected_province)
    except Exception as exc:
        _render_error(exc)
        return
    if selected_employee != "Tất cả":
        frame = frame[frame["ward_id"].isin(employee_options[selected_employee])]
    if ward_search:
        mask = (
            frame["ward_name"].astype(str).str.casefold().str.contains(ward_search, regex=False)
            | frame["cells7"].astype(str).str.casefold().str.contains(ward_search, regex=False)
        )
        frame = frame[mask]
    st.caption(f"{len(frame):,} sự cố trong khoảng {first} – {last}.")
    display_frame = frame.drop(columns=["ward_id"], errors="ignore")
    st.dataframe(display_frame, width="stretch", hide_index=True)
    st.download_button(
        "Tải CSV",
        display_frame.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"vhkt_{first}_{last}.csv",
        mime="text/csv",
        disabled=frame.empty,
    )


def _render_monthly_report(wards: pd.DataFrame) -> None:
    st.header("Báo cáo tháng")
    province = _province_filter(wards, "monthly_province")
    selected_province = None if province == "Tất cả" else province
    try:
        available_months = db.get_ward_day_months(selected_province)
        current_month = date.today().replace(day=1)
        month_options = {current_month.strftime("%Y-%m")}
        month_options.update(
            pd.Timestamp(value).date().replace(day=1).strftime("%Y-%m")
            for value in available_months
        )
        month_labels = sorted(month_options, reverse=True)
        selected_month_label = st.selectbox(
            "Tháng báo cáo",
            month_labels,
            index=0,
            key="report_month",
        )
        first, last = month_to_date_range(date.fromisoformat(f"{selected_month_label}-01"))
        _, _, _, daily = _load_kpi_inputs(first, last, selected_province)
        if daily.empty:
            st.info("Chưa có dữ liệu KPI trong tháng đã chọn.")
            return
        monthly = calculate_cumulative_kpi(daily, first, last)
    except Exception as exc:
        _render_error(exc)
        return

    province_rows = monthly[monthly["scope_type"] == "province"].copy()
    employee_rows = monthly[
        (monthly["scope_type"] == "employee") & monthly["scope_id"].notna()
    ].copy()
    employee_rows["scope_name"] = employee_rows.apply(
        lambda row: f"{row['scope_name'] or row['scope_id']} ({row['scope_id']}) — {row['province_name']}",
        axis=1,
    )
    province_rows["scope_label"] = "Tỉnh — " + province_rows["scope_name"].astype(str)
    employee_rows["scope_label"] = "Chuyên viên — " + employee_rows["scope_name"].astype(str)
    comparison_rows = pd.concat([province_rows, employee_rows], ignore_index=True)
    _show_primary_kpi_comparison(
        comparison_rows,
        "So sánh MTCL_2026 — tỉnh và chuyên viên",
        label="scope_label",
        metric="mtcl_2026",
    )
    _show_primary_kpi_comparison(
        comparison_rows,
        "So sánh TG-MLLTB — tỉnh và chuyên viên",
        label="scope_label",
        metric="tg_mllltb",
    )

    st.caption(
        f"Kỳ báo cáo: {first:%Y-%m-%d} đến {last:%Y-%m-%d} "
        f"({int(monthly['days_with_data'].max())} ngày có dữ liệu tối đa)."
    )
    st.subheader("Chi tiết theo tỉnh")
    _show_kpi_table(province_rows, scope_type="province")
    st.subheader("Chi tiết theo chuyên viên")
    _show_kpi_table(employee_rows, scope_type="employee")
    export_rows = pd.concat([province_rows, employee_rows], ignore_index=True)
    st.download_button(
        "Tải báo cáo CSV",
        export_rows.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"kpi_thang_{selected_month_label}.csv",
        mime="text/csv",
        disabled=export_rows.empty,
    )


def _render_admin(wards: pd.DataFrame) -> None:
    st.header("Quản trị")
    auth.render_admin_login()
    if not auth.is_admin():
        st.info("Các trang dữ liệu vẫn xem công khai. Đăng nhập admin để sửa phân công hoặc chạy thu thập.")
        return

    st.subheader("Quản lý phân công phường")
    editable = wards[admin.ASSIGNMENT_COLUMNS].copy()
    with st.form("ward_assignment_form"):
        edited = st.data_editor(
            editable,
            width="stretch",
            hide_index=True,
            disabled=["ward_id", "ward_code", "province_name", "ward_name"],
            column_config={
                "number_site": st.column_config.NumberColumn("Số trạm", min_value=0, step=1),
                "is_active": st.column_config.CheckboxColumn("Đang hoạt động"),
            },
        )
        save = st.form_submit_button("Lưu phân công")
    if save:
        try:
            count = admin.save_ward_assignments(edited)
        except Exception as exc:
            st.error(f"Không lưu được phân công: {exc}")
        else:
            st.success(f"Đã lưu {count} phường.")
            st.rerun()

    st.subheader("Chạy thu thập thủ công")
    task = st.radio("Nguồn dữ liệu", ["Phường (Playwright)", "VHKT"], horizontal=True)
    collect_date = st.date_input("Ngày cần thu thập", value=date.today() - timedelta(days=1), key="admin_collect_date")
    provinces = sorted(wards["province_name"].dropna().unique().tolist())
    default_province = "Nghe An" if "Nghe An" in provinces else (provinces[0] if provinces else "")
    collect_province = st.selectbox(
        "Tỉnh thu thập",
        provinces or [default_province],
        index=provinces.index(default_province) if default_province in provinces else 0,
        key="admin_collect_province",
    )
    if st.button("Chạy thu thập", type="primary"):
        try:
            with st.spinner(f"Đang thu thập {task}..."):
                if task == "Phường (Playwright)":
                    from database.collector_ward_day import collect
                else:
                    from database.collector_vhkt import collect
                result = collect(collect_date, collect_province)
        except Exception as exc:
            log.exception("Admin data collection failed")
            st.error(f"Thu thập thất bại: {exc}")
        else:
            st.success("Thu thập hoàn tất.")
            st.json(result)


def _format_metric(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.2f}"


def main() -> None:
    st.title("Dashboard KPI chuyên viên viễn thông")
    try:
        wards = db.get_wards()
    except Exception as exc:
        _render_error(exc)
        return

    with st.sidebar:
        st.caption("Các trang dữ liệu được xem công khai.")
        page = st.radio("Điều hướng", PAGE_NAMES, key="active_page")

    if page == "Tổng quan":
        _render_overview(wards)
    elif page == "KPI cá nhân":
        _render_employee_kpi(wards)
    elif page == "Dữ liệu VHKT":
        _render_vhkt(wards)
    elif page == "Báo cáo tháng":
        _render_monthly_report(wards)
    else:
        _render_admin(wards)


if __name__ == "__main__":
    main()
