"""Collect daily ward KPIs from SmartF with Playwright and upsert to PostgreSQL.

Run with ``python -m database.collector_ward_day``. The default report date is
yesterday; the downloaded CSV is mapped to the existing ``ward_day`` schema.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import io
import json
import logging
import os
from pathlib import Path
import re
import sys
import time

import pandas as pd
from playwright.sync_api import Download, Page, sync_playwright

from database import db
from database.proxy import playwright_proxy_from_env

log = logging.getLogger(__name__)
DEFAULT_BASE_URL = "https://smartf.mobifone.vn"
DEFAULT_PROVINCE = "Nghe An"
IFRAME = 'iframe[title="menu"]'
DOWNLOAD_TIMEOUT_MS = 180_000
POSTBACK_TIMEOUT_SEC = 180
REPORT_PATH = ("2026_V2", "PHƯỜNG_XÃ", "NGÀY")
BUTTON_VIEW = "#ReportViewerControl_ctl04_ctl00"
BUTTON_EXPORT = "#ReportViewerControl_ctl05_ctl04_ctl00_ButtonImgDown"
SELECTED_TEXT = "s => s.options[s.selectedIndex].text"
MAPPING_PATH = Path(__file__).resolve().parent.parent / "data" / "Map-Ward-Oracle_VS_Playwright.csv"
BROWSER_ARGS = [
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
    "--mute-audio",
]


class NoWardDayDataError(RuntimeError):
    """Raised when the SmartF report contains no rows for its parameters."""


def _parse_date(value: str | date | None) -> date:
    if value is None:
        return date.today() - timedelta(days=1)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Ngày phải có định dạng YYYY-MM-DD") from exc


def _normalize_space(value: str | None) -> str:
    return " ".join((value or "").split())


def _required_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise RuntimeError(f"Thiếu biến môi trường {names[0]} (xem .env.example)")


def _timeout_ms() -> int:
    raw = os.environ.get("WARD_DAY_TIMEOUT_SEC", "60")
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError("WARD_DAY_TIMEOUT_SEC phải là số nguyên dương") from exc
    if seconds < 1:
        raise ValueError("WARD_DAY_TIMEOUT_SEC phải là số nguyên dương")
    return seconds * 1000


def _frame(page: Page):
    frame_handle = page.locator(IFRAME).element_handle(timeout=10_000)
    if frame_handle is None:
        raise RuntimeError("Không tìm thấy iframe báo cáo SmartF")
    frame = frame_handle.content_frame()
    if frame is None:
        raise RuntimeError("Không đọc được nội dung iframe báo cáo SmartF")
    return frame


def _frame_text(page: Page, limit: int = 300) -> str:
    try:
        return _normalize_space(_frame(page).evaluate("() => document.body.innerText"))[:limit]
    except Exception as exc:
        return f"(không đọc được nội dung báo cáo: {str(exc)[:100]})"


def _wait_postback(page: Page, timeout_sec: int = POSTBACK_TIMEOUT_SEC, stable_sec: float = 1.5) -> None:
    end = time.monotonic() + timeout_sec
    idle_since = None
    while time.monotonic() < end:
        try:
            busy = _frame(page).evaluate(
                """() => {
                  let b = false;
                  try { b = Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack(); } catch (e) {}
                  const w = [...document.querySelectorAll('[id$="_AsyncWait_Wait"]')]
                    .some(e => getComputedStyle(e).display !== 'none' && e.offsetParent !== null);
                  return b || w;
                }"""
            )
        except Exception:
            busy = True
        if busy:
            idle_since = None
        else:
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since >= stable_sec:
                return
        page.wait_for_timeout(300)
    raise TimeoutError("Hết thời gian chờ SmartF xử lý báo cáo")


def _login(page: Page, username: str, password: str, base_url: str) -> None:
    page.goto(f"{base_url}/auth/login")
    page.get_by_role("textbox", name="UserName").fill(username)
    page.get_by_role("textbox", name="Password").fill(password)
    page.get_by_role("button", name="Đăng nhập").click()
    page.wait_for_url(lambda url: "/auth/login" not in str(url))
    page.wait_for_load_state("networkidle")
    log.info("Đăng nhập SmartF thành công")


def _open_report(page: Page, base_url: str, username: str, password: str) -> None:
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    if "/auth/login" in page.url:
        _login(page, username, password, base_url)

    telecom_link = page.locator("a").filter(has_text="Viễn Thông")
    if not telecom_link.first.is_visible():
        page.locator("#kt_aside_toggle").click()
    telecom_link.click()
    page.locator("a").filter(has_text="Chi Tiết").click()

    def menu_item(text: str):
        return page.get_by_text(re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)).filter(visible=True).first

    if not menu_item(REPORT_PATH[0]).is_visible():
        page.locator(".side-menu-button").first.click()
    for name in REPORT_PATH:
        log.info("Mở báo cáo ward_day: %s", name)
        menu_item(name).wait_for(state="visible")
        menu_item(name).click()


def _set_parameters(page: Page, province: str, report_date: date) -> None:
    frame = _frame(page)
    info = frame.evaluate(
        """(name) => {
          const n = s => s.replace(/\\s+/g, ' ').trim();
          const p = [...document.querySelectorAll('select')]
            .find(s => [...s.options].some(o => n(o.text) === name));
          const dates = [...document.querySelectorAll('input[type="text"]')]
            .filter(i => i.id && /^\\d{1,2}\\/\\d{1,2}\\/\\d{4}/.test(i.value))
            .map(i => i.id);
          return {province: p ? p.id : null, dates};
        }""",
        province,
    )
    if not info["province"] or not info["dates"]:
        raise RuntimeError(f"Không tìm thấy tham số tỉnh/ngày trong báo cáo ward_day: {info}")

    province_select = frame.locator("#" + info["province"])
    date_selectors = ["#" + selector for selector in info["dates"]]
    date_text = report_date.strftime("%m/%d/%Y")
    expected = (province, [date_text] * len(date_selectors))
    observed = None
    for attempt in range(1, 4):
        if _normalize_space(province_select.evaluate(SELECTED_TEXT)) != province:
            province_select.select_option(label=province)
            _wait_postback(page)
        for selector in date_selectors:
            box = frame.locator(selector)
            if box.input_value() != date_text:
                box.fill(date_text)
                box.press("Tab")
                _wait_postback(page)
        observed = (
            _normalize_space(province_select.evaluate(SELECTED_TEXT)),
            [frame.locator(selector).input_value() for selector in date_selectors],
        )
        if observed == expected:
            return
        log.warning("Tham số ward_day chưa khớp (lần %s/3): %s", attempt, observed)
    raise RuntimeError(f"Tham số báo cáo ward_day không khớp sau 3 lần: {observed}")


def _register_download_listener(page: Page, context, downloads: list[Download]) -> None:
    page.on("download", lambda download: downloads.append(download))
    context.on(
        "page",
        lambda popup: popup.on("download", lambda download: downloads.append(download)),
    )


def _browser_download(
    report_date: date,
    province: str,
    *,
    show_browser: bool = False,
) -> bytes:
    username = _required_env("SMARTF_USER", "WARD_DAY_USER")
    password = _required_env("SMARTF_PASS", "WARD_DAY_PASSWORD")
    base_url = os.environ.get("WARD_DAY_URL", "").strip().rstrip("/") or DEFAULT_BASE_URL
    timeout_ms = _timeout_ms()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not show_browser,
            args=BROWSER_ARGS,
            proxy=playwright_proxy_from_env(),
        )
        try:
            context = browser.new_context(accept_downloads=True, viewport={"width": 1600, "height": 900})
            try:
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                downloads: list[Download] = []
                _register_download_listener(page, context, downloads)
                _open_report(page, base_url, username, password)

                frame = page.frame_locator(IFRAME)
                frame.locator("select").first.wait_for(timeout=30_000)
                _wait_postback(page)
                _set_parameters(page, province, report_date)
                log.info("Chạy báo cáo ward_day: tỉnh=%s, ngày=%s", province, report_date.isoformat())
                frame.locator(BUTTON_VIEW).click()
                page.wait_for_timeout(2000)
                _wait_postback(page)
                frame.locator(BUTTON_EXPORT).wait_for(timeout=180_000)
                page.wait_for_timeout(1500)

                export_button = frame.locator(BUTTON_EXPORT)
                csv_link = frame.get_by_role("link", name="CSV (comma delimited)")
                for attempt in range(1, 31):
                    export_button.click()
                    try:
                        csv_link.wait_for(state="visible", timeout=4000)
                        break
                    except Exception:
                        if attempt % 5 == 0:
                            log.info("Đang chờ menu xuất CSV ward_day (lần %s/30)", attempt)
                        page.wait_for_timeout(3000)
                else:
                    raise RuntimeError(
                        f"Không mở được menu xuất CSV ward_day. Nội dung báo cáo: {_frame_text(page)!r}"
                    )
                csv_link.click()

                deadline = time.monotonic() + DOWNLOAD_TIMEOUT_MS / 1000
                while not downloads and time.monotonic() < deadline:
                    page.wait_for_timeout(500)
                if not downloads:
                    raise TimeoutError("Đã yêu cầu tải CSV ward_day nhưng không nhận được file")
                with Path(downloads[0].path()).open("rb") as stream:
                    return stream.read()
            finally:
                context.close()
        finally:
            browser.close()


def _mapping() -> dict[str, str]:
    try:
        frame = pd.read_csv(MAPPING_PATH, dtype=str, encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise RuntimeError(f"Không đọc được file mapping ward_day {MAPPING_PATH}: {exc}") from exc
    if list(frame.columns) != ["ward_day-Oracle", "Ward_day_Playwright"]:
        raise ValueError("File mapping ward_day phải có hai cột ward_day-Oracle, Ward_day_Playwright")
    frame = frame.dropna()
    mapping = {
        str(playwright).strip().upper(): (
            "traffic_all_gb"
            if str(oracle).strip().upper() == "SUM(TRAFFIC_ALL_GB)"
            else str(oracle).strip().lower()
        )
        for oracle, playwright in zip(frame["ward_day-Oracle"], frame["Ward_day_Playwright"])
    }
    if not mapping or len(mapping) != len(frame):
        raise ValueError("File mapping ward_day đang trống hoặc có tên cột Playwright bị trùng")
    required = set(db.WARD_DAY_COLUMNS[1:]) - {"stt"}
    missing_targets = required - {target.lower() for target in mapping.values()}
    if missing_targets:
        raise ValueError(f"File mapping ward_day thiếu các cột Oracle: {sorted(missing_targets)}")
    return mapping


def _datetime_id(value: str):
    text = str(value).strip()
    if not text:
        return None
    digits = re.sub(r"\.0+$", "", text)
    if digits.isdigit() and len(digits) == 10:
        return digits
    if digits.isdigit() and len(digits) == 8:
        return digits + "00"

    formats = (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    )
    for fmt in formats:
        parsed = pd.to_datetime(text, format=fmt, errors="coerce")
        if not pd.isna(parsed):
            return parsed.strftime("%Y%m%d%H")
    return None


def parse_report_csv(content: bytes) -> pd.DataFrame:
    """Drop SmartF's three preamble rows and map its CSV to the ward_day schema."""
    if not content:
        raise ValueError("CSV ward_day tải về đang trống")
    try:
        frame = pd.read_csv(
            io.BytesIO(content),
            dtype=str,
            encoding="utf-8-sig",
            keep_default_na=False,
            skiprows=3,
        )
    except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ValueError(f"Không đọc được CSV ward_day tải về: {exc}") from exc

    if frame.empty:
        raise NoWardDayDataError("Báo cáo ward_day không có dữ liệu cho tỉnh/ngày đã chọn")

    mapping = _mapping()
    source_columns = {str(column).strip().upper(): column for column in frame.columns}
    renames = {
        source_columns[playwright_name]: oracle_name
        for playwright_name, oracle_name in mapping.items()
        if playwright_name in source_columns
    }
    frame = frame.rename(columns=renames)
    required = set(db.WARD_DAY_COLUMNS[1:]) - {"stt"}
    missing = sorted(required - {str(column).strip().lower() for column in frame.columns})
    if missing:
        raise ValueError(f"CSV ward_day: thiếu cột theo mapping {missing}")
    datetime_column = next((c for c in frame.columns if str(c).strip().lower() == "datetime_id"), None)
    if datetime_column is None:
        raise ValueError("CSV ward_day: thiếu cột DATETIME_ID sau khi áp dụng mapping")
    frame[datetime_column] = frame[datetime_column].map(_datetime_id)
    frame["STT"] = range(1, len(frame) + 1)

    normalized, _ = db.prepare_ward_day(frame)
    if normalized.empty:
        raise NoWardDayDataError("Báo cáo ward_day không có bản ghi hợp lệ")
    return normalized


def collect(
    report_date: str | date | None = None,
    province: str | None = None,
    *,
    dry_run: bool = False,
    show_browser: bool = False,
) -> dict:
    selected_date = _parse_date(report_date)
    selected_province = (province or os.environ.get("WARD_DAY_PROVINCE") or DEFAULT_PROVINCE).strip()
    if not selected_province:
        raise ValueError("Tỉnh không được để trống")

    log.info(
        "Bắt đầu thu thập ward_day bằng Playwright: province=%s, date=%s",
        selected_province,
        selected_date.isoformat(),
    )
    content = _browser_download(selected_date, selected_province, show_browser=show_browser)
    frame = parse_report_csv(content)
    if dry_run:
        return {
            "status": "fetched",
            "date": selected_date.isoformat(),
            "province": selected_province,
            "rows_fetched": len(frame),
        }

    result = db.upsert_ward_day(frame)
    output = {
        "status": "success",
        "date": selected_date.isoformat(),
        "province": selected_province,
        "rows_fetched": len(frame),
        **result,
    }
    log.info("Thu thập ward_day bằng Playwright hoàn tất: %s", output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Ngày báo cáo, định dạng YYYY-MM-DD (mặc định: hôm qua)")
    parser.add_argument(
        "--province",
        help=f"Tỉnh cần lấy (mặc định: WARD_DAY_PROVINCE hoặc {DEFAULT_PROVINCE!r})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Chỉ tải/kiểm tra CSV, không ghi PostgreSQL")
    parser.add_argument("--show", action="store_true", help="Hiện cửa sổ trình duyệt để gỡ lỗi")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = collect(args.date, args.province, dry_run=args.dry_run, show_browser=args.show)
    except Exception:
        log.exception("Thu thập ward_day bằng Playwright thất bại")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
