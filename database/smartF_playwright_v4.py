"""
Tải lần lượt 2 báo cáo SmartF (Nghe An, ngày hôm qua) ra CSV bằng Playwright:
  1) KPI Trạm - NGÀY       -> kpi_tram_nghean_YYYYMMDD.csv
  2) VHKT Trạm - NGÀY      -> vhkt_tram_nghean_YYYYMMDD.csv
  3) Phường/Xã - NGÀY      -> ward_nghean_YYYYMMDD.csv

Tiết kiệm RAM: 1 trình duyệt, 1 tab chính, chạy tuần tự, đăng nhập 1 lần;
xong mỗi báo cáo thì đóng tab phụ và bỏ trang báo cáo nặng trước khi sang báo cáo kế.

Cài đặt (1 lần):
    pip install playwright
    playwright install chromium     (WSL: có thể cần playwright install-deps chromium)

Chạy:
    export SMARTF_USER=ten_dang_nhap
    export SMARTF_PASS='mat_khau'
    python smartF_playwright_v3.py                # cả 3 báo cáo, chạy ẩn
    python smartF_playwright_v3.py --only ward    # chỉ 1 báo cáo (kpi | vhkt | ward)
    python smartF_playwright_v3.py --show         # hiện trình duyệt để debug
"""
import getpass
import os
import re
import sys
import time
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

BASE = "https://smartf.mobifone.vn"
PROVINCE = "Nghe An"
REPORT_DATE = datetime.now() - timedelta(days=1)
DATE_FMT = "%m/%d/%Y"                      # giao diện SmartF dùng mm/dd/yyyy
WANT_DATE = REPORT_DATE.strftime(DATE_FMT)

IFRAME = 'iframe[title="menu"]'
DOWNLOAD_TIMEOUT_MS = 180_000

# Nút trong iframe ReportViewer (chuẩn SSRS, giống nhau ở mọi báo cáo).
# Ô Tỉnh và ô ngày được tự dò theo từng báo cáo (xem find_param_ids).
BTN_VIEW = "#ReportViewerControl_ctl04_ctl00"
BTN_EXPORT = "#ReportViewerControl_ctl05_ctl04_ctl00_ButtonImgDown"
SELECTED_TEXT = "s => s.options[s.selectedIndex].text"

REPORTS = [
    {"key": "kpi", "path": ("2026_V2", "TRẠM", "KPI", "NGÀY"),
     "output": f"kpi_tram_nghean_{REPORT_DATE:%Y%m%d}.csv"},
    {"key": "vhkt", "path": ("2026_V2", "TRẠM", "VHKT", "NGÀY"),
     "output": f"vhkt_tram_nghean_{REPORT_DATE:%Y%m%d}.csv"},
    {"key": "ward", "path": ("2026_V2", "PHƯỜNG_XÃ", "NGÀY"),
     "output": f"ward_nghean_{REPORT_DATE:%Y%m%d}.csv"},
]


def norm(s):
    """Chuẩn hóa khoảng trắng (SmartF dùng \\xa0 trong 'Nghe\\xa0An')."""
    return " ".join((s or "").split())


def report_frame(page):
    return page.locator(IFRAME).element_handle(timeout=10_000).content_frame()


def frame_text(page, n=200):
    try:
        return norm(report_frame(page).evaluate("() => document.body.innerText"))[:n]
    except Exception as e:
        return f"(không đọc được: {str(e)[:60]})"


def wait_postback(page, timeout_s=180, stable_s=1.5):
    """Chờ SSRS/ASP.NET AJAX xử lý xong (đổi tỉnh, điền ngày, View Report).
    Phải yên liên tục `stable_s` giây; khung đang nạp lại cũng tính là bận."""
    end = time.time() + timeout_s
    idle_since = None
    while time.time() < end:
        try:
            busy = report_frame(page).evaluate(
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
            idle_since = idle_since or time.time()
            if time.time() - idle_since >= stable_s:
                return
        page.wait_for_timeout(300)
    raise RuntimeError("Hết thời gian chờ SSRS xử lý xong")


# ---------------------------------------------------------------- đăng nhập / menu
def login(page, user, pwd):
    page.goto(f"{BASE}/auth/login")
    page.get_by_role("textbox", name="UserName").fill(user)
    page.get_by_role("textbox", name="Password").fill(pwd)
    page.get_by_role("button", name="Đăng nhập").click()
    page.wait_for_url(lambda u: "/auth/login" not in u)
    page.wait_for_load_state("networkidle")
    print("Đăng nhập xong:", page.url)


def open_report(page, path, user, pwd):
    """Về trang chủ portal (nạp sạch) rồi đi theo menu tới báo cáo."""
    page.goto(BASE)
    page.wait_for_load_state("networkidle")
    if "/auth/login" in page.url:           # phiên hết hạn
        login(page, user, pwd)

    viet = page.locator("a").filter(has_text="Viễn Thông")
    if not viet.first.is_visible():
        page.locator("#kt_aside_toggle").click()
    viet.click()
    page.locator("a").filter(has_text="Chi Tiết").click()

    def item(text):
        return (page.get_by_text(re.compile(rf"^\s*{text}\s*$", re.I))
                .filter(visible=True).first)

    if not item(path[0]).is_visible():
        page.locator(".side-menu-button").first.click()
    for name in path:
        print("  Menu:", name)
        item(name).wait_for(state="visible")
        item(name).click()


# ---------------------------------------------------------------- tham số + export
def find_param_ids(page):
    """Tìm id ô Tỉnh (select có lựa chọn PROVINCE) và các ô ngày (text dạng mm/dd/yyyy).
    Mỗi báo cáo có thể đánh số ctlXX khác nhau nên không dùng id cố định."""
    info = report_frame(page).evaluate(
        """(name) => {
          const n = s => s.replace(/\\s+/g, ' ').trim();
          const s = [...document.querySelectorAll('select')]
            .find(s => [...s.options].some(o => n(o.text) === name));
          const dates = [...document.querySelectorAll('input[type="text"]')]
            .filter(i => i.id && /^\\d{1,2}\\/\\d{1,2}\\/\\d{4}/.test(i.value)).map(i => i.id);
          return {province: s ? s.id : null, dates};
        }""",
        PROVINCE,
    )
    if not info["province"] or not info["dates"]:
        raise RuntimeError(f"Không tìm thấy ô Tỉnh/ngày trong báo cáo: {info}")
    return "#" + info["province"], ["#" + d for d in info["dates"]]


def set_params(page, fl):
    """Điền tỉnh + ngày, chờ SSRS xử lý sau mỗi thay đổi, kiểm tra lại giá trị thật."""
    prov_id, date_ids = find_param_ids(page)
    prov = fl.locator(prov_id)
    for attempt in range(1, 4):
        if norm(prov.evaluate(SELECTED_TEXT)) != PROVINCE:
            prov.select_option(label=PROVINCE)
            wait_postback(page)              # đổi tỉnh -> danh sách Xã nạp lại
        for sel in date_ids:
            box = fl.locator(sel)
            if box.input_value() != WANT_DATE:
                box.fill(WANT_DATE)
                box.press("Tab")
                wait_postback(page)
        got = (norm(prov.evaluate(SELECTED_TEXT)),
               [fl.locator(s).input_value() for s in date_ids])
        if got == (PROVINCE, [WANT_DATE] * len(date_ids)):
            print(f"  Tham số: {got[0]}, {got[1]}")
            return
        print(f"  Tham số chưa khớp (lần {attempt}): {got}")
    raise RuntimeError(f"Tham số chưa đúng sau 3 lần thử: {got}")


def export_csv(page, fl, downloads, output):
    export_btn = fl.locator(BTN_EXPORT)
    csv_link = fl.get_by_role("link", name="CSV (comma delimited)")
    for attempt in range(1, 31):             # menu Export chỉ mở được khi báo cáo sẵn sàng
        export_btn.click()
        try:
            csv_link.wait_for(state="visible", timeout=4000)
            break
        except Exception:
            if attempt % 5 == 0:
                print(f"  Mở menu Export (lần {attempt}/30)...")
            page.wait_for_timeout(3000)
    else:
        raise RuntimeError(f"Menu Export không mở ra. Trang: {frame_text(page, 300)!r}")
    csv_link.click()

    waited = 0
    while not downloads and waited < DOWNLOAD_TIMEOUT_MS:
        page.wait_for_timeout(500)
        waited += 500
    if not downloads:
        raise RuntimeError("Đã bấm CSV nhưng không nhận được sự kiện tải file")
    downloads[0].save_as(output)
    with open(output, "rb") as f:
        n_lines = sum(1 for _ in f)
    print(f"  Đã lưu: {output} ({n_lines} dòng)")


def download_report(page, downloads, rep, user, pwd):
    downloads.clear()
    open_report(page, rep["path"], user, pwd)

    fl = page.frame_locator(IFRAME)
    fl.locator("select").first.wait_for(timeout=30_000)
    wait_postback(page)
    set_params(page, fl)

    print("  View Report...")
    fl.locator(BTN_VIEW).click()
    page.wait_for_timeout(2000)              # cho postback kịp bắt đầu
    wait_postback(page)
    fl.locator(BTN_EXPORT).wait_for(timeout=180_000)
    page.wait_for_timeout(1500)

    export_csv(page, fl, downloads, rep["output"])


def cleanup(page, ctx):
    """Giải phóng RAM giữa 2 báo cáo."""
    for pg in list(ctx.pages):
        if pg is not page:
            try:
                pg.close()
            except Exception:
                pass
    try:
        page.goto("about:blank")
    except Exception:
        pass


# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    show = "--show" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1].lower() if "--only" in sys.argv else None
    reports = [r for r in REPORTS if only in (None, r["key"])]
    if not reports:
        sys.exit(f"--only phải là: {', '.join(r['key'] for r in REPORTS)}")

    user = os.environ.get("SMARTF_USER") or input("Username: ")
    pwd = os.environ.get("SMARTF_PASS") or getpass.getpass("Password: ")

    failed = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not show,
            args=["--disable-gpu", "--disable-dev-shm-usage", "--disable-extensions",
                  "--disable-background-networking", "--disable-sync",
                  "--no-first-run", "--mute-audio"],
        )
        ctx = browser.new_context(accept_downloads=True,
                                  viewport={"width": 1600, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(60_000)

        # Bắt tải file trên MỌI tab (bấm CSV có thể mở tab phụ rồi tải từ đó)
        downloads = []
        watch = lambda pg: pg.on("download", lambda d: downloads.append(d))
        watch(page)
        ctx.on("page", watch)

        try:
            login(page, user, pwd)
            for rep in reports:
                print(f"\n=== {rep['key'].upper()}: {' > '.join(rep['path'])} ===")
                try:
                    download_report(page, downloads, rep, user, pwd)
                except Exception as e:
                    failed.append(rep["key"])
                    print(f"LỖI ở {rep['key']}: {str(e)[:500]}")
                    print("Trang đang hiện:", frame_text(page, 300))
                    try:
                        page.screenshot(path=f"error_{rep['key']}.png")
                        print(f"Đã lưu error_{rep['key']}.png")
                    except Exception:
                        pass
                finally:
                    cleanup(page, ctx)
        finally:
            browser.close()

    print(f"\nHoàn tất sau {time.time() - t0:.0f} giây."
          + (f" Lỗi: {', '.join(failed)}" if failed else ""))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
