"""Collect the daily VHKT report from SmartF and upsert it into PostgreSQL.

Run with ``python -m database.collector_vhkt``. The default report date is
yesterday, following the working flow in ``database/smartF_playwright_v3.py``.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

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
REPORT_PATH = ("2026_V2", "TRẠM", "VHKT", "NGÀY")
PARAM_PREFIX = "#ReportViewerControl_ctl04_"
SELECT_PROVINCE = PARAM_PREFIX + "ctl09_ddValue"
DATE_FROM = PARAM_PREFIX + "ctl03_txtValue"
DATE_TO = PARAM_PREFIX + "ctl05_txtValue"
BUTTON_VIEW = PARAM_PREFIX + "ctl00"
BUTTON_EXPORT = "#ReportViewerControl_ctl05_ctl04_ctl00_ButtonImgDown"
SELECTED_TEXT = "s => s.options[s.selectedIndex].text"
BROWSER_ARGS = [
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
    "--mute-audio",
]


class NoVhktDataError(RuntimeError):
    """Raised when the report has no records for its parameters."""


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
    raw = os.environ.get("VHKT_TIMEOUT_SEC", "60")
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError("VHKT_TIMEOUT_SEC phải là số nguyên dương") from exc
    if seconds < 1:
        raise ValueError("VHKT_TIMEOUT_SEC phải là số nguyên dương")
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
    """Wait until the SSRS/ASP.NET frame is idle continuously."""
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
        log.info("Mở báo cáo VHKT: %s", name)
        menu_item(name).wait_for(state="visible")
        menu_item(name).click()


def _set_parameters(page: Page, province: str, report_date: date) -> None:
    frame = page.frame_locator(IFRAME)
    province_select = frame.locator(SELECT_PROVINCE)
    date_text = report_date.strftime("%m/%d/%Y")
    expected = (province, date_text, date_text)
    observed = None

    for attempt in range(1, 4):
        if _normalize_space(province_select.evaluate(SELECTED_TEXT)) != province:
            province_select.select_option(label=province)
            _wait_postback(page)
        for selector in (DATE_FROM, DATE_TO):
            box = frame.locator(selector)
            if box.input_value() != date_text:
                box.fill(date_text)
                box.press("Tab")
                _wait_postback(page)
        observed = (
            _normalize_space(province_select.evaluate(SELECTED_TEXT)),
            frame.locator(DATE_FROM).input_value(),
            frame.locator(DATE_TO).input_value(),
        )
        if observed == expected:
            return
        log.warning("Tham số VHKT chưa khớp (lần %s/3): %s", attempt, observed)
    raise RuntimeError(f"Tham số báo cáo VHKT không khớp sau 3 lần: {observed}")


def _register_download_listener(page: Page, context, downloads: list[Download]) -> None:
    page.on("download", lambda download: downloads.append(download))
    context.on(
        "page",
        lambda popup: popup.on("download", lambda download: downloads.append(download)),
    )


def parse_report_csv(content: bytes) -> pd.DataFrame:
    """Decode and validate the downloaded CSV against the existing VHKT schema."""
    if not content:
        raise ValueError("CSV VHKT tải về đang trống")
        
    try:
        # Cách 1: Thử đọc bình thường (đề phòng trường hợp file chuẩn không có header rác)
        frame = pd.read_csv(io.BytesIO(content), dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except (UnicodeDecodeError, pd.errors.ParserError):
        try:
            # Cách 2: Tự động bỏ qua các dòng metadata rác của SmartF (tìm dòng có 13 cột trở lên)
            # Thường SmartF chèn 3 dòng tiêu đề phụ ở đầu, tiêu đề thật nằm ở dòng 4 (skiprows=3)
            frame = pd.read_csv(
                io.BytesIO(content), 
                dtype=str, 
                encoding="utf-8-sig", 
                keep_default_na=False,
                skiprows=3
            )
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            raise ValueError(f"Không đọc được CSV VHKT tải về: {exc}") from exc

    if frame.empty:
        raise NoVhktDataError("Báo cáo VHKT không có dữ liệu cho tỉnh/ngày đã chọn")
        
    # In debug để bạn kiểm tra các cột nhận diện được (có thể xóa sau khi chạy thành công)
    print(f"Các cột đọc được từ CSV: {list(frame.columns)}")
    
    normalized, _ = db.prepare_vhkt(frame)
    if normalized.empty:
        raise NoVhktDataError("Báo cáo VHKT không có bản ghi hợp lệ")
    return normalized



def _browser_download(
    report_date: date,
    province: str,
    *,
    show_browser: bool = False,
) -> bytes:
    username = _required_env("SMARTF_USER", "VHKT_USER")
    password = _required_env("SMARTF_PASS", "VHKT_PASSWORD")
    base_url = os.environ.get("VHKT_URL", "").strip().rstrip("/") or DEFAULT_BASE_URL
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
                log.info("Chạy báo cáo VHKT: tỉnh=%s, ngày=%s", province, report_date.isoformat())
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
                            log.info("Đang chờ menu xuất CSV VHKT (lần %s/30)", attempt)
                        page.wait_for_timeout(3000)
                else:
                    raise RuntimeError(
                        f"Không mở được menu xuất CSV. Nội dung báo cáo: {_frame_text(page)!r}"
                    )
                csv_link.click()

                deadline = time.monotonic() + DOWNLOAD_TIMEOUT_MS / 1000
                while not downloads and time.monotonic() < deadline:
                    page.wait_for_timeout(500)
                if not downloads:
                    raise TimeoutError("Đã yêu cầu tải CSV VHKT nhưng không nhận được file")
                with Path(downloads[0].path()).open("rb") as stream:
                    return stream.read()
            finally:
                context.close()
        finally:
            browser.close()


def collect(
    report_date: str | date | None = None,
    province: str | None = None,
    *,
    dry_run: bool = False,
    show_browser: bool = False,
) -> dict:
    selected_date = _parse_date(report_date)
    selected_province = (province or os.environ.get("VHKT_PROVINCE") or DEFAULT_PROVINCE).strip()
    if not selected_province:
        raise ValueError("Tỉnh không được để trống")

    log.info(
        "Bắt đầu thu thập VHKT: province=%s, date=%s",
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

    result = db.upsert_vhkt_day(frame)
    output = {
        "status": "success",
        "date": selected_date.isoformat(),
        "province": selected_province,
        "rows_fetched": len(frame),
        **result,
    }
    log.info("Thu thập VHKT hoàn tất: %s", output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Ngày báo cáo, định dạng YYYY-MM-DD (mặc định: hôm qua)")
    parser.add_argument("--province", help=f"Tỉnh cần lấy (mặc định: VHKT_PROVINCE hoặc {DEFAULT_PROVINCE!r})")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ tải/kiểm tra CSV, không ghi PostgreSQL")
    parser.add_argument("--show", action="store_true", help="Hiện cửa sổ trình duyệt để gỡ lỗi")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = collect(args.date, args.province, dry_run=args.dry_run, show_browser=args.show)
    except Exception:
        log.exception("Thu thập VHKT thất bại")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
