"""Scheduled maintenance jobs for the KPI database.

Run monthly retention cleanup with ``python -m jobs.jobs cleanup`` on the first
day of each month. The previous month's monthly KPI is stored before source rows
from month N-3 are deleted.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import logging
import sys

import pandas as pd
from psycopg2.extras import Json, execute_values

from KPI.kpi import KPI_VALUE_COLUMNS, calculate_daily_kpi, calculate_monthly_kpi
from database import db


log = logging.getLogger(__name__)


def month_n_minus_three(as_of: date) -> tuple[date, date]:
    """Return the inclusive date range for the month three months before as_of."""
    month_serial = (as_of.year - 1) * 12 + as_of.month - 1 - 3
    year_index, month_index = divmod(month_serial, 12)
    first = date(year_index + 1, month_index + 1, 1)
    next_year_index, next_month_index = divmod(month_serial + 1, 12)
    last = date(next_year_index + 1, next_month_index + 1, 1) - timedelta(days=1)
    return first, last


def _summary_values(row: pd.Series) -> dict:
    values = {}
    for column in KPI_VALUE_COLUMNS:
        value = row.get(column)
        if pd.isna(value):
            values[column] = None
        elif column == "site_count":
            values[column] = int(value)
        else:
            values[column] = float(value)
    return values


def _upsert_monthly_summary(
    cursor,
    month: date,
    monthly: pd.DataFrame,
    wards: pd.DataFrame,
) -> int:
    ward_rows = monthly[
        (monthly["scope_type"] == "ward") & monthly["scope_id"].notna()
    ].merge(
        wards[["ward_id", "employee_code"]],
        left_on="scope_id",
        right_on="ward_id",
        how="left",
        validate="many_to_one",
    )
    if ward_rows.empty:
        raise RuntimeError(
            f"Không có KPI theo phường cho tháng {month:%Y-%m}; giữ nguyên dữ liệu nguồn."
        )
    records = []
    for _, row in ward_rows.iterrows():
        records.append(
            (
                month,
                int(row["scope_id"]),
                None if pd.isna(row["employee_code"]) else str(row["employee_code"]),
                int(row["days_with_data"]),
                Json(_summary_values(row), dumps=json.dumps),
            )
        )
    execute_values(
        cursor,
        """INSERT INTO monthly_summary
               (month, ward_id, employee_code, days_with_data, kpi_values, is_final)
           VALUES %s
           ON CONFLICT (month, ward_id) DO UPDATE SET
               employee_code = EXCLUDED.employee_code,
               days_with_data = EXCLUDED.days_with_data,
               kpi_values = EXCLUDED.kpi_values,
               is_final = TRUE,
               computed_at = now()""",
        records,
        template="(%s, %s, %s, %s, %s, TRUE)",
    )
    return len(records)


def run_monthly_retention_cleanup(
    as_of: date | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Summarize and remove raw ward_day/VHKT rows for month N-3 on day one."""
    as_of = as_of or date.today()
    if as_of.day != 1:
        return {"status": "skipped", "reason": "not_first_day", "as_of": as_of.isoformat()}

    first, last = month_n_minus_three(as_of)
    with db.connect() as conn:
        counts = db.query_dataframe(
            """SELECT
                   (SELECT count(*) FROM ward_day WHERE data_date BETWEEN %s AND %s) AS ward_day_rows,
                   (SELECT count(*) FROM vhkt_day WHERE data_date BETWEEN %s AND %s) AS vhkt_rows""",
            (first, last, first, last),
            conn=conn,
        ).iloc[0]
        ward_day_count = int(counts["ward_day_rows"])
        vhkt_count = int(counts["vhkt_rows"])
        if ward_day_count == 0 and vhkt_count == 0:
            return {
                "status": "already_clean",
                "month": first.strftime("%Y-%m"),
                "summary_rows": 0,
                "ward_day_rows_deleted": 0,
                "vhkt_rows_deleted": 0,
            }
        if ward_day_count == 0:
            raise RuntimeError(
                f"Không có dữ liệu ward_day để tổng hợp tháng {first:%Y-%m}; "
                "giữ nguyên dữ liệu VHKT, không xóa nguồn."
            )

        wards = db.get_wards(conn=conn)
        ward_day_rows = db.get_ward_day(first, last, conn=conn)
        vhkt_rows = db.get_vhkt_day(first, last, conn=conn)
        daily = calculate_daily_kpi(ward_day_rows, vhkt_rows, wards)
        if daily.empty:
            raise RuntimeError(
                f"Không tính được KPI tháng {first:%Y-%m}; giữ nguyên dữ liệu nguồn."
            )
        monthly = calculate_monthly_kpi(daily, first)
        summary_count = len(
            monthly[(monthly["scope_type"] == "ward") & monthly["scope_id"].notna()]
        )
        if summary_count == 0:
            raise RuntimeError(
                f"Không có KPI theo phường cho tháng {first:%Y-%m}; giữ nguyên dữ liệu nguồn."
            )

        if dry_run:
            return {
                "status": "dry_run",
                "month": first.strftime("%Y-%m"),
                "ward_day_rows": ward_day_count,
                "vhkt_rows": vhkt_count,
                "summary_rows": summary_count,
            }

        with conn.cursor() as cursor:
            summary_count = _upsert_monthly_summary(cursor, first, monthly, wards)
            cursor.execute(
                "DELETE FROM ward_day WHERE data_date BETWEEN %s AND %s",
                (first, last),
            )
            deleted_ward_day = cursor.rowcount
            cursor.execute(
                "DELETE FROM vhkt_day WHERE data_date BETWEEN %s AND %s",
                (first, last),
            )
            deleted_vhkt = cursor.rowcount

    result = {
        "status": "cleaned",
        "month": first.strftime("%Y-%m"),
        "summary_rows": summary_count,
        "ward_day_rows_deleted": deleted_ward_day,
        "vhkt_rows_deleted": deleted_vhkt,
    }
    log.info("Monthly retention cleanup complete: %s", result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    cleanup = subparsers.add_parser(
        "cleanup",
        help="tổng hợp và dọn dữ liệu nguồn tháng N-3 (chỉ ngày đầu tháng)",
    )
    cleanup.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        help="ngày hiện tại giả lập dạng YYYY-MM-DD (mặc định: hôm nay)",
    )
    cleanup.add_argument(
        "--dry-run",
        action="store_true",
        help="tính số bản ghi sẽ tổng hợp/xóa nhưng không ghi hoặc xóa dữ liệu",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = run_monthly_retention_cleanup(args.as_of, dry_run=args.dry_run)
    except Exception:
        log.exception("Monthly retention cleanup failed")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
