from __future__ import annotations

import shlex
import sys

from playwright.sync_api import Error


def launch_chromium(chromium, *, headless: bool, args: list[str], proxy: dict | None):
    try:
        return chromium.launch(headless=headless, args=args, proxy=proxy)
    except Error as exc:
        if "Executable doesn't exist" not in str(exc):
            raise
        command = (
            f"PLAYWRIGHT_BROWSERS_PATH=0 {shlex.quote(sys.executable)} "
            "-m playwright install chromium"
        )
        raise RuntimeError(
            "Không tìm thấy Chromium của Playwright. Hãy cài browser bằng tài khoản "
            f"chạy dashboard/collector: {command}"
        ) from exc
