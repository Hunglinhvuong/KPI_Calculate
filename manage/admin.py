"""Admin-only operations for ward assignments and manual data collection."""
from __future__ import annotations

import pandas as pd
from psycopg2.extras import execute_values

from database import db
from manage import auth


ASSIGNMENT_COLUMNS = [
    "ward_id",
    "ward_code",
    "province_name",
    "ward_name",
    "employee_code",
    "employee_name",
    "number_site",
    "is_active",
]


def save_ward_assignments(frame: pd.DataFrame) -> int:
    auth.require_admin()
    missing = [column for column in ASSIGNMENT_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Thiếu cột phân công {missing}")

    updates = frame[["ward_id", "employee_code", "employee_name", "number_site", "is_active"]].copy()
    if updates["ward_id"].isna().any() or updates["ward_id"].duplicated().any():
        raise ValueError("ward_id phải có giá trị và không được trùng")
    updates["ward_id"] = pd.to_numeric(updates["ward_id"], errors="raise").astype("int64")
    updates["employee_code"] = updates["employee_code"].astype("string").str.strip().replace("", pd.NA)
    updates["employee_name"] = updates["employee_name"].astype("string").str.strip().replace("", pd.NA)
    sites = pd.to_numeric(updates["number_site"], errors="coerce")
    if sites.isna().any() or (sites < 0).any() or (sites % 1 != 0).any():
        raise ValueError("number_site phải là số nguyên không âm")
    updates["number_site"] = sites.astype("int64")
    if updates["is_active"].isna().any() or not updates["is_active"].map(
        lambda value: isinstance(value, bool) or type(value).__name__ == "bool_"
    ).all():
        raise ValueError("is_active phải là boolean")

    if updates.empty:
        return 0
    values = [
        (
            int(row.ward_id),
            None if pd.isna(row.employee_code) else str(row.employee_code),
            None if pd.isna(row.employee_name) else str(row.employee_name),
            int(row.number_site),
            bool(row.is_active),
        )
        for row in updates.itertuples(index=False)
    ]
    with db.connect() as conn, conn.cursor() as cursor:
        execute_values(
            cursor,
            """UPDATE ward AS target
               SET employee_code = source.employee_code,
                   employee_name = source.employee_name,
                   number_site = source.number_site,
                   is_active = source.is_active,
                   updated_at = now()
               FROM (VALUES %s) AS source(ward_id, employee_code, employee_name, number_site, is_active)
               WHERE target.ward_id = source.ward_id""",
            values,
            template="(%s, %s, %s, %s, %s)",
            page_size=len(values),
        )
        if cursor.rowcount != len(values):
            raise ValueError("Một hoặc nhiều ward_id không còn tồn tại; không lưu phân công")
    return len(values)
