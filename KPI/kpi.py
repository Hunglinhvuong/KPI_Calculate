"""Daily, monthly, and cumulative KPI calculations for wards and staff."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
import re
import unicodedata

import pandas as pd


@dataclass(frozen=True)
class VhktThresholds:
    overdue_target: float = 0.56
    overdue_worst: float = 0.73
    ratio_target: float = 0.043
    ratio_worst: float = 0.06
    time_target: float = 83.84
    time_worst: float = 109.85
    wo_completion: float = 100.0

    def __post_init__(self):
        for name in (
            "overdue_target", "overdue_worst", "ratio_target",
            "ratio_worst", "time_target", "time_worst", "wo_completion",
        ):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise ValueError(f"{name} phải là số hữu hạn")
        if self.overdue_target >= self.overdue_worst:
            raise ValueError("overdue_target phải nhỏ hơn overdue_worst")
        if self.ratio_target >= self.ratio_worst:
            raise ValueError("ratio_target phải nhỏ hơn ratio_worst")
        if self.time_target >= self.time_worst:
            raise ValueError("time_target phải nhỏ hơn time_worst")


DEFAULT_THRESHOLDS = VhktThresholds()

KPI_VALUE_COLUMNS = [
    "htmt_kpi",
    "htmt_qos",
    "ml_overdue",
    "ml_overdue_tlht",
    "ml_ratio",
    "ml_ratio_tlht",
    "ml_time",
    "ml_time_tlht",
    "wo_tlht",
    "tg_mllltb",
    "htmt_vhkt",
    "htmt_anm",
    "mtcl_2026",
    "traffic_all_gb",
    "site_count",
]
OUTPUT_COLUMNS = [
    "data_date",
    "province_name",
    "scope_type",
    "scope_id",
    "scope_name",
    "days_with_data",
    *KPI_VALUE_COLUMNS,
]
_COLUMN_SPECIAL = {"sum(traffic_all_gb)": "traffic_all_gb"}


def _norm(value) -> str:
    text = unicodedata.normalize("NFD", str(value)).replace("đ", "d").replace("Đ", "D")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _columns(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for column in frame.columns:
        normalized = str(column).strip().casefold()
        rename[column] = _COLUMN_SPECIAL.get(normalized, normalized)
    return frame.rename(columns=rename).copy()


def _require(frame: pd.DataFrame, columns: list[str], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{name}: thiếu cột {missing}")


def _dates(frame: pd.DataFrame, name: str) -> pd.Series:
    if "data_date" in frame:
        parsed = pd.to_datetime(frame["data_date"], errors="coerce")
    elif "datetime_id" in frame:
        raw = pd.to_numeric(frame["datetime_id"], errors="coerce")
        valid = raw.notna() & (raw % 1 == 0)
        parsed = pd.to_datetime(
            raw.where(valid).astype("Int64").astype("string"),
            format="%Y%m%d%H",
            errors="coerce",
        )
    elif name == "vhkt" and "sdate" in frame:
        parsed = pd.to_datetime(frame["sdate"], errors="coerce", dayfirst=True)
    else:
        raise ValueError(f"{name}: cần có cột data_date hoặc datetime_id")
    if parsed.isna().any():
        raise ValueError(f"{name}: có {int(parsed.isna().sum())} ngày dữ liệu không hợp lệ")
    return parsed.dt.date


def _clean_numeric(frame: pd.DataFrame, columns: list[str], name: str) -> None:
    for column in columns:
        raw = frame[column]
        parsed = pd.to_numeric(raw, errors="coerce")
        nonempty = raw.notna() & raw.astype("string").str.strip().ne("")
        if (nonempty & parsed.isna()).any():
            raise ValueError(f"{name}: cột {column} có giá trị không phải số")
        finite = parsed.dropna().map(math.isfinite)
        if not finite.all():
            raise ValueError(f"{name}: cột {column} phải có giá trị hữu hạn")
        frame[column] = parsed


def completion_rate(actual: float, target: float, worst: float) -> float:
    """Completion for lower-is-better measures, with a 70-to-0 middle band."""
    if not all(math.isfinite(value) for value in (actual, target, worst)) or target >= worst:
        raise ValueError("Giá trị thực tế/ngưỡng không hợp lệ")
    if actual <= target:
        return 100.0
    if actual >= worst:
        return 0.0
    return 70.0 + 30.0 * (actual - worst) / (target - worst)


def _prepare_wards(wards: pd.DataFrame) -> pd.DataFrame:
    frame = _columns(wards)
    _require(frame, ["ward_id", "province_name", "ward_code", "number_site"], "ward")
    if "employee_code" not in frame:
        frame["employee_code"] = pd.NA
    if "employee_name" not in frame:
        frame["employee_name"] = pd.NA
    frame["number_site"] = pd.to_numeric(frame["number_site"], errors="coerce")
    if frame["number_site"].isna().any():
        raise ValueError("ward: number_site phải có giá trị để tính tỷ lệ VHKT")
    if not frame["number_site"].map(math.isfinite).all():
        raise ValueError("ward: number_site phải là số hữu hạn")
    if (frame["number_site"] < 0).any():
        raise ValueError("ward: number_site không được âm")
    frame["_join_key"] = [
        (_norm(province), _norm(code))
        for province, code in zip(frame["province_name"], frame["ward_code"])
    ]
    if frame["_join_key"].duplicated().any():
        raise ValueError("ward: trùng (province_name, ward_code) sau chuẩn hóa")
    nonnull_ids = frame.loc[frame["ward_id"].notna(), "ward_id"]
    if nonnull_ids.duplicated().any():
        raise ValueError("ward: ward_id không duy nhất")
    return frame


def _attach_ward(
    frame: pd.DataFrame,
    wards: pd.DataFrame,
    province_column: str,
    ward_column: str,
    name: str,
) -> pd.DataFrame:
    frame = frame.copy()
    frame["_join_key"] = [
        (_norm(province), _norm(code))
        for province, code in zip(frame[province_column], frame[ward_column])
    ]
    lookup = wards.set_index("_join_key", drop=False)
    unmatched = sorted(set(frame.loc[~frame["_join_key"].isin(lookup.index), "_join_key"]))
    if unmatched:
        raise ValueError(f"{name}: không tìm thấy {len(unmatched)} phường trong ward, ví dụ {unmatched[:5]}")
    metadata = wards.set_index("_join_key")
    for column in ("ward_id", "number_site", "employee_code", "employee_name", "province_name", "ward_code"):
        frame[column] = frame["_join_key"].map(metadata[column])
    return frame.drop(columns="_join_key")


def _prepare_ward_day(ward_day: pd.DataFrame, wards: pd.DataFrame) -> pd.DataFrame:
    frame = _columns(ward_day)
    _require(
        frame,
        ["province", "district", "traffic_all_gb", "htmt_kpi", "htmt_qos", "htmt_anm"],
        "ward_day",
    )
    frame["data_date"] = _dates(frame, "ward_day")
    _clean_numeric(
        frame,
        ["traffic_all_gb", "htmt_kpi", "htmt_qos", "htmt_anm"],
        "ward_day",
    )
    frame = _attach_ward(frame, wards, "province", "district", "ward_day")
    if frame.duplicated(["data_date", "ward_id"]).any():
        raise ValueError("ward_day: có nhiều bản ghi cùng phường/ngày; không thể tính KPI ngày duy nhất")
    return frame


def _prepare_vhkt(vhkt_day: pd.DataFrame, wards: pd.DataFrame) -> pd.DataFrame:
    frame = _columns(vhkt_day)
    _require(frame, ["province_name", "ward_name", "dat", "minute_number"], "vhkt_day")
    frame["data_date"] = _dates(frame, "vhkt")
    _clean_numeric(frame, ["minute_number"], "vhkt_day")
    frame = _attach_ward(frame, wards, "province_name", "ward_name", "vhkt_day")
    return frame


def _weighted_average(values: pd.Series, weights: pd.Series) -> float | None:
    valid = values.notna() & weights.notna() & (weights >= 0)
    denominator = float(weights[valid].sum())
    if denominator <= 0:
        return None
    return float((values[valid] * weights[valid]).sum() / denominator)


def _calculate_group(
    ward_rows: pd.DataFrame,
    vhkt_rows: pd.DataFrame,
    thresholds: VhktThresholds,
) -> dict:
    sites = float(ward_rows["number_site"].sum())
    if sites <= 0:
        raise ValueError("Tổng number_site của phạm vi KPI phải lớn hơn 0")
    overdue_count = int(vhkt_rows["dat"].astype("string").str.strip().str.upper().eq("N").sum())
    event_count = len(vhkt_rows)
    ml_overdue = overdue_count / sites * 100
    ml_ratio = event_count / sites
    ml_time = (
        float(vhkt_rows["minute_number"].mean())
        if event_count and vhkt_rows["minute_number"].notna().any()
        else 0.0
    )
    overdue_tlht = completion_rate(ml_overdue, thresholds.overdue_target, thresholds.overdue_worst)
    ratio_tlht = completion_rate(ml_ratio, thresholds.ratio_target, thresholds.ratio_worst)
    time_tlht = completion_rate(ml_time, thresholds.time_target, thresholds.time_worst)
    htmt_vhkt = 0.2 * (overdue_tlht + ratio_tlht + time_tlht) + 0.4 * thresholds.wo_completion
    traffic = ward_rows["traffic_all_gb"].fillna(0)
    htmt_kpi = _weighted_average(ward_rows["htmt_kpi"], traffic)
    htmt_qos = _weighted_average(ward_rows["htmt_qos"], traffic)
    htmt_anm = float(ward_rows["htmt_anm"].fillna(0).sum())
    if htmt_kpi is None or htmt_qos is None:
        mtcl = None
    else:
        mtcl = max(0.0, 0.2 * htmt_qos + 0.4 * htmt_kpi + 0.4 * htmt_vhkt - htmt_anm)
    return {
        "htmt_kpi": htmt_kpi,
        "htmt_qos": htmt_qos,
        "ml_overdue": ml_overdue,
        "ml_overdue_tlht": overdue_tlht,
        "ml_ratio": ml_ratio,
        "ml_ratio_tlht": ratio_tlht,
        "ml_time": ml_time,
        "ml_time_tlht": time_tlht,
        "wo_tlht": thresholds.wo_completion,
        "tg_mllltb": (overdue_tlht + ratio_tlht + time_tlht) / 3,
        "htmt_vhkt": htmt_vhkt,
        "htmt_anm": htmt_anm,
        "mtcl_2026": mtcl,
        "traffic_all_gb": float(traffic.sum()),
        "site_count": sites,
    }


def calculate_daily_kpi(
    ward_day: pd.DataFrame,
    vhkt_day: pd.DataFrame,
    wards: pd.DataFrame,
    thresholds: VhktThresholds = DEFAULT_THRESHOLDS,
) -> pd.DataFrame:
    """Calculate daily KPI per ward, employee, and province.

    VHKT rates use the sum of ``ward.number_site`` in each scope. With no VHKT
    records, counts and average duration are zero, yielding full time completion.
    """
    ward_master = _prepare_wards(wards)
    source = _prepare_ward_day(ward_day, ward_master)
    events = _prepare_vhkt(vhkt_day, ward_master)
    event_groups = {
        key: group
        for key, group in events.groupby(["data_date", "ward_id"], dropna=False)
    }
    rows = []
    scope_definitions = (
        ("ward", ["data_date", "ward_id"], "ward_id", "ward_code"),
        ("employee", ["data_date", "province_name", "employee_code"], "employee_code", "employee_name"),
        ("province", ["data_date", "province_name"], "province_name", "province_name"),
    )

    for data_date, daily_rows in source.groupby("data_date", sort=True):
        for scope_type, _, _, _ in scope_definitions:
            if scope_type == "ward":
                groups = daily_rows.groupby(["ward_id"], dropna=False, sort=False)
            elif scope_type == "employee":
                groups = daily_rows.groupby(["province_name", "employee_code"], dropna=False, sort=False)
            else:
                groups = daily_rows.groupby(["province_name"], dropna=False, sort=False)

            for group_key, ward_rows in groups:
                if not isinstance(group_key, tuple):
                    group_key = (group_key,)
                if scope_type == "ward":
                    sample = ward_rows.iloc[0]
                    scope_id = sample["ward_id"]
                    scope_name = sample["ward_code"]
                    province_name = sample["province_name"]
                    event_key = (data_date, scope_id)
                    related_events = event_groups.get(event_key, events.iloc[0:0])
                elif scope_type == "employee":
                    province_name, employee_code = group_key
                    scope_id = employee_code
                    employee_names = ward_rows["employee_name"].dropna().unique().tolist()
                    scope_name = employee_names[0] if employee_names else None
                    if len(employee_names) > 1:
                        raise ValueError(f"employee_code {employee_code!r} có nhiều employee_name")
                    related_events = events[
                        events["ward_id"].isin(ward_rows["ward_id"])
                        & events["data_date"].eq(data_date)
                    ]
                else:
                    province_name = group_key[0]
                    scope_id = province_name
                    scope_name = province_name
                    related_events = events[
                        events["ward_id"].isin(ward_rows["ward_id"])
                        & events["data_date"].eq(data_date)
                    ]
                values = _calculate_group(ward_rows, related_events, thresholds)
                rows.append({
                    "data_date": data_date,
                    "province_name": province_name,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "scope_name": scope_name,
                    "days_with_data": 1,
                    **values,
                })

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def _mean_daily_kpi(
    daily_kpi: pd.DataFrame,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    month: date | None = None,
) -> pd.DataFrame:
    frame = _columns(daily_kpi)
    _require(frame, ["data_date", "province_name", "scope_type", "scope_id", "scope_name"], "daily_kpi")
    frame["data_date"] = _dates(frame, "daily_kpi")
    if month is not None:
        month_start = date(month.year, month.month, 1)
        next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        start_date, end_date = month_start, next_month - timedelta(days=1)
    if start_date is not None:
        frame = frame[frame["data_date"] >= start_date]
    if end_date is not None:
        frame = frame[frame["data_date"] <= end_date]
    if frame.empty:
        return pd.DataFrame(columns=["province_name", "scope_type", "scope_id", "scope_name", "days_with_data", *KPI_VALUE_COLUMNS])

    numeric_columns = [column for column in KPI_VALUE_COLUMNS if column in frame.columns]
    _clean_numeric(frame, numeric_columns, "daily_kpi")
    group_columns = ["province_name", "scope_type", "scope_id", "scope_name"]
    aggregations = {
        column: (column, "max" if column == "site_count" else "mean")
        for column in numeric_columns
    }
    result = (
        frame.groupby(group_columns, dropna=False, sort=False)
        .agg(
            days_with_data=("data_date", "nunique"),
            **aggregations,
        )
        .reset_index()
    )
    return result[["province_name", "scope_type", "scope_id", "scope_name", "days_with_data", *numeric_columns]]


def calculate_monthly_kpi(daily_kpi: pd.DataFrame, month: date) -> pd.DataFrame:
    """Return the arithmetic mean of daily KPI values within a calendar month."""
    result = _mean_daily_kpi(daily_kpi, month=month)
    result.insert(0, "month", date(month.year, month.month, 1))
    return result


def month_to_date_range(month: date, as_of: date | None = None) -> tuple[date, date]:
    """Return a selected month's reporting range, capped at today for open months."""
    first = date(month.year, month.month, 1)
    next_month = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    month_end = next_month - timedelta(days=1)
    return first, min(month_end, as_of or date.today())


def calculate_cumulative_kpi(
    daily_kpi: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Return the arithmetic mean of daily KPI values in an inclusive date range."""
    if start_date > end_date:
        raise ValueError("start_date không được sau end_date")
    result = _mean_daily_kpi(daily_kpi, start_date=start_date, end_date=end_date)
    result.insert(0, "start_date", start_date)
    result.insert(1, "end_date", end_date)
    return result
