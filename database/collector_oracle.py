"""Collect daily ward KPI rows from Oracle and upsert them into PostgreSQL.

Run with ``python -m database.collector_oracle``. The default collection date is
yesterday and the source is expected to contain only the 00:00 row for a day.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import logging
import os
import re
import sys
import time

import oracledb
import pandas as pd

from database import db

log = logging.getLogger(__name__)
DEFAULT_TABLE = "MOBIFONEREPORT_HCHC.VMTCL_2026_WARD_DAY_V2"
DEFAULT_PROVINCE = "Nghe An"
TABLE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_$#]*(?:\.[A-Za-z][A-Za-z0-9_$#]*)*\Z")


class NoOracleDataError(RuntimeError):
    """Raised when Oracle returns no rows for the requested province and date."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Thiếu biến môi trường {name} (xem .env.example)")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} phải là số nguyên dương") from exc
    if value < 1:
        raise ValueError(f"{name} phải là số nguyên dương")
    return value


def _parse_date(value: str | date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Ngày phải có định dạng YYYY-MM-DD") from exc


def datetime_id_for_day(value: str | date | None = None) -> int:
    """Return the source key for a date; the source contains the 00:00 row only."""
    collection_date = _parse_date(value) if value is not None else date.today() - timedelta(days=1)
    return int(collection_date.strftime("%Y%m%d") + "00")


def _table_name() -> str:
    table = os.environ.get("ORACLE_TABLE", DEFAULT_TABLE).strip()
    if not TABLE_PATTERN.fullmatch(table):
        raise ValueError("ORACLE_TABLE chỉ được chứa tên bảng Oracle không trích dẫn (schema.table)")
    return table


def fetch_oracle_data(
    datetime_id: int,
    province: str,
    *,
    attempts: int | None = None,
) -> pd.DataFrame:
    """Fetch one province/day from Oracle, retrying transient Oracle errors."""
    user = _required_env("ORACLE_USER")
    password = _required_env("ORACLE_PASSWORD")
    dsn = _required_env("ORACLE_DSN")
    timeout = _positive_int("ORACLE_TIMEOUT_SEC", 60)
    max_attempts = attempts if attempts is not None else _positive_int("COLLECT_MAX_RETRY", 3)
    if max_attempts < 1:
        raise ValueError("attempts phải là số nguyên dương")
    if not province.strip():
        raise ValueError("Tỉnh không được để trống")
    table = _table_name()
    query = f"SELECT * FROM {table} WHERE province = :province AND datetime_id = :datetime_id"

    for attempt in range(1, max_attempts + 1):
        try:
            with oracledb.connect(
                user=user,
                password=password,
                dsn=dsn,
                tcp_connect_timeout=timeout,
            ) as connection:
                connection.call_timeout = timeout * 1000
                with connection.cursor() as cursor:
                    cursor.execute(query, province=province, datetime_id=datetime_id)
                    columns = [column[0] for column in cursor.description]
                    rows = cursor.fetchall()
            if not rows:
                raise NoOracleDataError(
                    f"Oracle không có dữ liệu cho province={province!r}, DATETIME_ID={datetime_id}"
                )
            return pd.DataFrame.from_records(rows, columns=columns)
        except NoOracleDataError:
            raise
        except oracledb.Error:
            if attempt == max_attempts:
                log.exception(
                    "Oracle query thất bại sau %d lần thử (DATETIME_ID=%s, province=%s)",
                    attempt,
                    datetime_id,
                    province,
                )
                raise
            log.warning(
                "Oracle query lỗi ở lần %d/%d; sẽ thử lại",
                attempt,
                max_attempts,
                exc_info=True,
            )
            time.sleep(min(attempt, 5))
    raise RuntimeError("Không thể hoàn tất truy vấn Oracle")


def collect(
    collection_date: str | date | None = None,
    province: str | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Fetch and optionally persist the previous day's 00:00 KPI rows."""
    datetime_id = datetime_id_for_day(collection_date)
    selected_province = (province or os.environ.get("ORACLE_PROVINCE") or DEFAULT_PROVINCE).strip()
    log.info(
        "Bắt đầu thu thập Oracle: province=%s, DATETIME_ID=%s, thin mode=%s",
        selected_province,
        datetime_id,
        oracledb.is_thin_mode(),
    )
    frame = fetch_oracle_data(datetime_id, selected_province)
    if dry_run:
        return {
            "status": "fetched",
            "date": datetime.strptime(str(datetime_id), "%Y%m%d%H").date().isoformat(),
            "datetime_id": datetime_id,
            "province": selected_province,
            "rows_fetched": len(frame),
        }

    result = db.upsert_ward_day(frame)
    output = {
        "status": "success",
        "date": datetime.strptime(str(datetime_id), "%Y%m%d%H").date().isoformat(),
        "datetime_id": datetime_id,
        "province": selected_province,
        "rows_fetched": len(frame),
        **result,
    }
    log.info("Thu thập Oracle hoàn tất: %s", output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Ngày cần chạy lại, định dạng YYYY-MM-DD (mặc định: hôm qua)")
    parser.add_argument(
        "--province",
        help=f"Tỉnh cần lấy (mặc định: ORACLE_PROVINCE hoặc {DEFAULT_PROVINCE!r})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ truy vấn Oracle, không ghi PostgreSQL",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = collect(args.date, args.province, dry_run=args.dry_run)
    except Exception:
        log.exception("Thu thập Oracle thất bại")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
