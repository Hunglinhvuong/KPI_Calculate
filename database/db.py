"""Kết nối PostgreSQL, nạp CSV và upsert dữ liệu (ward, ward_day, vhkt_day).

Dùng làm thư viện (collector gọi upsert_* với DataFrame) hoặc chạy dòng lệnh:

    python -m database.db init
    python -m database.db ward data/ward.csv
    python -m database.db ward-day data/sample_ward_day.csv
    python -m database.db vhkt data/sample_vhkt_day.csv
    python -m database.db relink

Quy ước nhận diện phường: Oracle.DISTRICT = VHKT.WARD_NAME = ward.ward_code (kèm tỉnh).
Việc so khớp bỏ qua dấu tiếng Việt, hoa/thường và khoảng trắng thừa; dữ liệu gốc
trong bảng vẫn giữ nguyên bản.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

try:  # .env là tùy chọn; có thể dùng biến môi trường thật
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

WARD_COLUMNS = [
    "ward_id", "ward_code", "province_name", "ward_name",
    "employee_code", "employee_name", "is_active",
]
# Cột tùy chọn trong ward.csv: nếu CSV không có cột này thì giá trị đã lưu trong DB được giữ nguyên.
WARD_OPTIONAL_COLUMNS = ["number_site"]

# Thứ tự cột Oracle sau khi đổi tên: stt, datetime_id, province, district, rồi các chỉ số số học.
WARD_DAY_COLUMNS = [
    "stt", "datetime_id", "province", "district",
    "mtcl_2026", "htmt_kpi", "htmt_qos", "htmt_vhkt", "traffic_all_gb",
    "cssr", "pasr", "cdr", "padr", "hosr", "prb_dl", "dkd", "badlink_4g",
    "vn_cssr", "vn_call_drop", "mc_staon_num_r", "usr_dl_thp_4g", "usr_dl_thp_5g", "rsrp",
]
WARD_DAY_NUMERIC = WARD_DAY_COLUMNS[4:]
# Cột tùy chọn: nếu nguồn không có cột này thì giá trị đã lưu trong DB được giữ nguyên.
WARD_DAY_OPTIONAL_NUMERIC = ["htmt_anm"]
WARD_DAY_OPTIONAL_TEXT = {"danh_gia": 10}  # tên cột -> độ dài tối đa (VARCHAR(10))
WARD_DAY_OPTIONAL = list(WARD_DAY_OPTIONAL_TEXT) + WARD_DAY_OPTIONAL_NUMERIC
ORACLE_HEADER_SPECIAL = {"SUM(TRAFFIC_ALL_GB)": "traffic_all_gb"}

VHKT_COLUMNS = [
    "province_name", "ward_name", "cells7", "sdate", "edate", "minute_number",
    "network", "ma_phong_xl", "ma_to_xl", "uu_tien", "dat",
]
VHKT_TS_FORMATS = ["%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"]


# --------------------------------------------------------------------------- kết nối
@contextmanager
def connect():
    """Mở kết nối PostgreSQL từ biến môi trường; commit nếu thành công, rollback nếu lỗi."""
    missing = [k for k in ("PG_DB", "PG_USER") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Thiếu biến môi trường: {', '.join(missing)} (xem .env.example)")
    conn = psycopg2.connect(
        host=os.environ.get("PG_HOST", "localhost"),
        port=int(os.environ.get("PG_PORT", "5432")),
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ.get("PG_PASSWORD") or None,
        connect_timeout=int(os.environ.get("PG_CONNECT_TIMEOUT", "10")),
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _use(conn):
    if conn is not None:
        yield conn
    else:
        with connect() as c:
            yield c


def query_dataframe(query: str, params: tuple = (), conn=None) -> pd.DataFrame:
    """Run a read-only parameterized query and return its rows as a DataFrame."""
    with _use(conn) as connection, connection.cursor() as cur:
        cur.execute(query, params)
        columns = [column[0] for column in cur.description]
        return pd.DataFrame.from_records(cur.fetchall(), columns=columns)


def get_wards(active_only: bool = False, conn=None) -> pd.DataFrame:
    where = "WHERE is_active" if active_only else ""
    return query_dataframe(
        f"""SELECT ward_id, ward_code, province_name, ward_name, employee_code,
                   employee_name, number_site, is_active, updated_at
            FROM ward {where}
            ORDER BY province_name, ward_name""",
        conn=conn,
    )


def get_ward_day(start_date, end_date, province: str | None = None, conn=None) -> pd.DataFrame:
    conditions = ["data_date BETWEEN %s AND %s"]
    params = [start_date, end_date]
    if province:
        conditions.append("province = %s")
        params.append(province)
    return query_dataframe(
        f"""SELECT * FROM ward_day
            WHERE {' AND '.join(conditions)}
            ORDER BY data_date, province, district""",
        tuple(params),
        conn=conn,
    )


def get_ward_day_months(province: str | None = None) -> list:
    conditions = []
    params = []
    if province:
        conditions.append("province = %s")
        params.append(province)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    frame = query_dataframe(
        f"""SELECT DISTINCT date_trunc('month', data_date)::date AS month
            FROM ward_day {where}
            ORDER BY month DESC""",
        tuple(params),
    )
    return frame["month"].tolist()


def get_vhkt_day(start_date, end_date, province: str | None = None, conn=None) -> pd.DataFrame:
    conditions = ["data_date BETWEEN %s AND %s"]
    params = [start_date, end_date]
    if province:
        conditions.append("province_name = %s")
        params.append(province)
    return query_dataframe(
        f"""SELECT ward_id, province_name, ward_name, cells7, sdate, edate, data_date,
                   minute_number, network, ma_phong_xl, ma_to_xl, uu_tien, dat, collected_at
            FROM vhkt_day
            WHERE {' AND '.join(conditions)}
            ORDER BY sdate DESC, province_name, ward_name""",
        tuple(params),
        conn=conn,
    )


def init_schema(conn=None) -> None:
    """Tạo bảng/chỉ mục từ schema.sql (chạy lại an toàn)."""
    with _use(conn) as c, c.cursor() as cur:
        cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- tiện ích
def norm_name(s) -> str:
    """Chuẩn hóa tên để so khớp: bỏ dấu, đ->d, hạ chữ thường, gọn khoảng trắng."""
    s = unicodedata.normalize("NFD", str(s)).replace("đ", "d").replace("Đ", "D")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip().casefold()


def _norm_columns(df: pd.DataFrame, special: dict | None = None) -> pd.DataFrame:
    special = special or {}
    mapping = {}
    for c in df.columns:
        key = str(c).strip().upper()
        mapping[c] = special.get(key, key.lower())
    return df.rename(columns=mapping)


def _require(df: pd.DataFrame, cols: list[str], what: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{what}: thiếu cột {missing}")


def _clean_text(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip()
    return s.mask((s == "").fillna(False))  # rỗng -> NA


def _required_text(df: pd.DataFrame, cols: list[str], what: str) -> None:
    for c in cols:
        df[c] = _clean_text(df[c])
        if df[c].isna().any():
            n = int(df[c].isna().sum())
            raise ValueError(f"{what}: cột {c} có {n} dòng trống")


def _numeric(df: pd.DataFrame, cols: list[str], what: str) -> None:
    for c in cols:
        s = df[c].astype("string").str.strip()
        nonempty = (s.notna() & (s != "")).fillna(False).astype(bool)
        num = pd.to_numeric(df[c], errors="coerce")
        bad = nonempty & num.isna()
        if bad.any():
            examples = df.loc[bad, c].astype(str).head(3).tolist()
            raise ValueError(f"{what}: cột {c} có {int(bad.sum())} giá trị không phải số, ví dụ {examples}")
        df[c] = num


def _py(v):
    if pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if hasattr(v, "item"):
        return v.item()
    return v


def _records(df: pd.DataFrame, cols: list[str]) -> list[tuple]:
    return [tuple(_py(v) for v in row) for row in df[cols].itertuples(index=False, name=None)]


def _parse_ts(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip()
    out = pd.to_datetime(s, format=VHKT_TS_FORMATS[0], errors="coerce")
    for fmt in VHKT_TS_FORMATS[1:]:
        missing = out.isna() & s.notna() & (s != "")
        if not missing.any():
            break
        out = out.where(~missing, pd.to_datetime(s, format=fmt, errors="coerce"))
    return out


def _read_csv(path) -> pd.DataFrame:
    # utf-8-sig: ward.csv có BOM; dtype=str để không làm hỏng số/mã (vd. DATETIME_ID).
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)


def _parse_bool(s: pd.Series, what: str) -> pd.Series:
    mapping = {"": True, "true": True, "t": True, "1": True, "yes": True, "y": True,
               "false": False, "f": False, "0": False, "no": False, "n": False}
    key = s.astype("string").str.strip().str.lower().fillna("")
    bad = ~key.isin(mapping)
    if bad.any():
        raise ValueError(f"{what}: is_active không hợp lệ: {s[bad].head(3).tolist()}")
    return key.map(mapping).astype(bool)  # ô trống = đang hoạt động


# --------------------------------------------------------------------------- chuẩn bị dữ liệu (không cần DB)
def prepare_ward(df: pd.DataFrame) -> pd.DataFrame:
    df = _norm_columns(df)
    _require(df, WARD_COLUMNS, "ward")
    optional = [c for c in WARD_OPTIONAL_COLUMNS if c in df.columns]
    df = df[WARD_COLUMNS + optional].copy()
    _required_text(df, ["ward_code", "province_name", "ward_name"], "ward")
    if "number_site" in optional:
        _numeric(df, ["number_site"], "ward")
        ns = df["number_site"]
        if (ns.notna() & ((ns % 1 != 0) | (ns < 0))).any():  # ô trống (NaN) là hợp lệ
            raise ValueError("ward: number_site phải là số nguyên không âm (hoặc để trống)")
        df["number_site"] = ns.astype("Int64")
    df["employee_code"] = _clean_text(df["employee_code"])
    df["employee_name"] = _clean_text(df["employee_name"])
    ids = pd.to_numeric(df["ward_id"], errors="coerce")
    if ids.isna().any() or (ids % 1 != 0).any():
        raise ValueError("ward: ward_id phải là số nguyên, không được trống")
    df["ward_id"] = ids.astype("int64")
    df["is_active"] = _parse_bool(df["is_active"], "ward")
    if df["ward_id"].duplicated().any():
        raise ValueError(f"ward: trùng ward_id {df.loc[df['ward_id'].duplicated(), 'ward_id'].tolist()}")
    keys = df["province_name"].map(norm_name) + "|" + df["ward_code"].map(norm_name)
    if keys.duplicated().any():
        raise ValueError(f"ward: trùng (tỉnh, ward_code) sau chuẩn hóa: {df.loc[keys.duplicated(), 'ward_code'].tolist()}")
    return df.reset_index(drop=True)


def prepare_ward_day(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Chuẩn hóa dữ liệu Oracle. Trả về (DataFrame, số dòng trùng khóa đã loại)."""
    df = _norm_columns(df, ORACLE_HEADER_SPECIAL)
    _require(df, WARD_DAY_COLUMNS, "ward_day")
    optional = [c for c in WARD_DAY_OPTIONAL if c in df.columns]
    df = df[WARD_DAY_COLUMNS + optional].copy()
    _required_text(df, ["province", "district"], "ward_day")
    for col, max_len in WARD_DAY_OPTIONAL_TEXT.items():
        if col in optional:
            df[col] = _clean_text(df[col])  # ô trống -> NULL
            too_long = df[col].str.len() > max_len
            if too_long.fillna(False).any():
                raise ValueError(f"ward_day: cột {col} dài quá {max_len} ký tự, ví dụ {df.loc[too_long.fillna(False), col].head(3).tolist()}")

    num = pd.to_numeric(df["datetime_id"], errors="coerce")
    ok = num.notna() & (num % 1 == 0) & num.between(1_000_000_000, 9_999_999_999)
    ts = pd.to_datetime(num.where(ok).astype("Int64").astype("string"), format="%Y%m%d%H", errors="coerce")
    bad = ts.isna()
    if bad.any():
        raise ValueError(
            f"ward_day: {int(bad.sum())} dòng có DATETIME_ID không đúng dạng YYYYMMDDHH, "
            f"ví dụ {df.loc[bad, 'datetime_id'].astype(str).head(3).tolist()}"
        )
    df["datetime_id"] = num.astype("int64")
    df["stt"] = pd.to_numeric(df["stt"], errors="coerce").astype("Int64")
    _numeric(df, WARD_DAY_NUMERIC + [c for c in WARD_DAY_OPTIONAL_NUMERIC if c in optional], "ward_day")

    before = len(df)
    df = df.drop_duplicates(["province", "district", "datetime_id"], keep="last")
    dropped = before - len(df)
    if dropped:
        log.warning("ward_day: loại %d dòng trùng (province, district, datetime_id) trong cùng lần nạp", dropped)
    return df.reset_index(drop=True), dropped


def prepare_vhkt(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    df = _norm_columns(df)
    _require(df, VHKT_COLUMNS, "vhkt_day")
    df = df[VHKT_COLUMNS].copy()
    _required_text(df, ["province_name", "ward_name", "cells7"], "vhkt_day")
    for c in ["network", "ma_phong_xl", "ma_to_xl", "uu_tien", "dat"]:
        df[c] = _clean_text(df[c])
    _numeric(df, ["minute_number"], "vhkt_day")

    raw_s, raw_e = df["sdate"], df["edate"]
    df["sdate"] = _parse_ts(raw_s)
    df["edate"] = _parse_ts(raw_e)
    bad_s = df["sdate"].isna()
    if bad_s.any():
        raise ValueError(f"vhkt_day: {int(bad_s.sum())} dòng có SDATE trống/sai định dạng, ví dụ {raw_s[bad_s].astype(str).head(3).tolist()}")
    e_given = raw_e.astype("string").str.strip().fillna("") != ""
    bad_e = df["edate"].isna() & e_given
    if bad_e.any():
        raise ValueError(f"vhkt_day: {int(bad_e.sum())} dòng có EDATE sai định dạng, ví dụ {raw_e[bad_e].astype(str).head(3).tolist()}")

    key = pd.DataFrame({
        "p": df["province_name"], "w": df["ward_name"], "c": df["cells7"], "s": df["sdate"],
        "e": df["edate"].fillna(pd.Timestamp.max), "n": df["network"].fillna(""),
    })
    dup = key.duplicated(keep="last")
    dropped = int(dup.sum())
    if dropped:
        log.warning("vhkt_day: loại %d dòng trùng khóa trong cùng lần nạp", dropped)
    return df[~dup].reset_index(drop=True), dropped


# --------------------------------------------------------------------------- ghép phường
def _ward_lookup(cur) -> dict[tuple[str, str], int]:
    cur.execute("SELECT ward_id, province_name, ward_code FROM ward")
    return {(norm_name(p), norm_name(c)): wid for wid, p, c in cur.fetchall()}


def _attach_ward_id(df: pd.DataFrame, lookup: dict, prov_col: str, code_col: str) -> list[tuple[str, str]]:
    ids = [lookup.get((norm_name(p), norm_name(c))) for p, c in zip(df[prov_col], df[code_col])]
    df["ward_id"] = pd.array(ids, dtype="Int64")
    unmatched = sorted(set(zip(df.loc[df["ward_id"].isna(), prov_col], df.loc[df["ward_id"].isna(), code_col])))
    if unmatched:
        log.warning("Không ghép được %d phường với bảng ward: %s", len(unmatched), unmatched[:10])
    return unmatched


def relink_unmatched(conn=None) -> dict:
    """Ghép lại ward_id cho các dòng còn NULL (chạy sau khi cập nhật ward.csv)."""
    result = {}
    with _use(conn) as c, c.cursor() as cur:
        lookup = _ward_lookup(cur)
        for table, prov, code in (("ward_day", "province", "district"), ("vhkt_day", "province_name", "ward_name")):
            cur.execute(f"SELECT id, {prov}, {code} FROM {table} WHERE ward_id IS NULL")
            updates = [(i, lookup[(norm_name(p), norm_name(w))])
                       for i, p, w in cur.fetchall() if (norm_name(p), norm_name(w)) in lookup]
            if updates:
                execute_values(
                    cur,
                    f"UPDATE {table} t SET ward_id = v.ward_id FROM (VALUES %s) AS v(id, ward_id) WHERE t.id = v.id",
                    updates,
                )
            result[table] = len(updates)
    return result


# --------------------------------------------------------------------------- upsert
def _count(rows) -> tuple[int, int]:
    inserted = sum(1 for (is_new,) in rows if is_new)
    return inserted, len(rows) - inserted


def upsert_wards(df: pd.DataFrame, conn=None) -> dict:
    df = prepare_ward(df)
    cols = WARD_COLUMNS + [c for c in WARD_OPTIONAL_COLUMNS if c in df.columns]
    updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in cols if col != "ward_id")
    with _use(conn) as c, c.cursor() as cur:
        rows = execute_values(
            cur,
            f"""INSERT INTO ward ({', '.join(cols)}) VALUES %s
                ON CONFLICT (ward_id) DO UPDATE SET {updates}, updated_at = now()
                RETURNING (xmax = 0)""",
            _records(df, cols), fetch=True,
        )
        # ward_id được nạp tường minh nên cần đồng bộ sequence để thêm phường mới không bị trùng id.
        cur.execute("SELECT setval(pg_get_serial_sequence('ward', 'ward_id'), GREATEST((SELECT max(ward_id) FROM ward), 1))")
        inserted, updated = _count(rows)
        relinked = relink_unmatched(c)
    return {"rows_in": len(df), "inserted": inserted, "updated": updated, "relinked": relinked}


def upsert_ward_day(df: pd.DataFrame, conn=None) -> dict:
    df, dropped = prepare_ward_day(df)
    with _use(conn) as c, c.cursor() as cur:
        unmatched = _attach_ward_id(df, _ward_lookup(cur), "province", "district")
        cols = WARD_DAY_COLUMNS + [c for c in WARD_DAY_OPTIONAL if c in df.columns] + ["ward_id"]
        updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in cols if col not in ("province", "district", "datetime_id"))
        rows = execute_values(
            cur,
            f"""INSERT INTO ward_day ({', '.join(cols)}) VALUES %s
                ON CONFLICT ON CONSTRAINT uq_ward_day_source DO UPDATE SET {updates}, collected_at = now()
                RETURNING (xmax = 0)""",
            _records(df, cols), page_size=1000, fetch=True,
        )
    inserted, updated = _count(rows)
    return {"rows_in": len(df) + dropped, "duplicates_dropped": dropped,
            "inserted": inserted, "updated": updated, "unmatched_wards": unmatched}


def upsert_vhkt_day(df: pd.DataFrame, conn=None) -> dict:
    df, dropped = prepare_vhkt(df)
    with _use(conn) as c, c.cursor() as cur:
        unmatched = _attach_ward_id(df, _ward_lookup(cur), "province_name", "ward_name")
        cols = VHKT_COLUMNS + ["ward_id"]
        updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in
                            ("minute_number", "ma_phong_xl", "ma_to_xl", "uu_tien", "dat", "ward_id"))
        rows = execute_values(
            cur,
            f"""INSERT INTO vhkt_day ({', '.join(cols)}) VALUES %s
                ON CONFLICT (province_name, ward_name, cells7, sdate,
                             COALESCE(edate, 'infinity'::timestamp), COALESCE(network, ''))
                DO UPDATE SET {updates}, collected_at = now()
                RETURNING (xmax = 0)""",
            _records(df, cols), page_size=1000, fetch=True,
        )
    inserted, updated = _count(rows)
    return {"rows_in": len(df) + dropped, "duplicates_dropped": dropped,
            "inserted": inserted, "updated": updated, "unmatched_wards": unmatched}


# --------------------------------------------------------------------------- nạp từ CSV
def load_wards_csv(path, conn=None) -> dict:
    return upsert_wards(_read_csv(path), conn)


def load_ward_day_csv(path, conn=None) -> dict:
    return upsert_ward_day(_read_csv(path), conn)


def load_vhkt_csv(path, conn=None) -> dict:
    return upsert_vhkt_day(_read_csv(path), conn)


# --------------------------------------------------------------------------- dòng lệnh
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m database.db", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="tạo bảng từ schema.sql")
    for name, helptext in (("ward", "nạp ward.csv"), ("ward-day", "nạp CSV Oracle (WARD_DAY)"), ("vhkt", "nạp CSV VHKT")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("csv")
    sub.add_parser("relink", help="ghép lại ward_id cho các dòng chưa ghép được")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "init":
        init_schema()
        result = {"schema": "ok"}
    elif args.cmd == "ward":
        result = load_wards_csv(args.csv)
    elif args.cmd == "ward-day":
        result = load_ward_day_csv(args.csv)
    elif args.cmd == "vhkt":
        result = load_vhkt_csv(args.csv)
    else:
        result = relink_unmatched()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
