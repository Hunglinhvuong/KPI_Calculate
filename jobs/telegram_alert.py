"""Send a Telegram alert when a scheduled KPI collector fails."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import logging
import os
import sys
import urllib.error
import urllib.request


log = logging.getLogger(__name__)
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TIMEOUT_SEC = 10


def send_telegram_alert(unit: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID trong .env")

    now = datetime.now().astimezone()
    failed_at = now.isoformat(timespec="seconds")
    data_date = (now.date() - timedelta(days=1)).isoformat()
    message = (
        "🚨 KPI collector thu thập dữ liệu thất bại\n"
        f"Collector: {unit}\n"
        f"Ngày dữ liệu: {data_date}\n"
        f"Thời điểm: {failed_at}\n"
        f"Xem chi tiết: journalctl -u {unit}"
    )
    request = urllib.request.Request(
        TELEGRAM_API_URL.format(token=token),
        data=json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TELEGRAM_TIMEOUT_SEC) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Telegram API trả về HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Không thể kết nối Telegram ({type(exc).__name__})") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("Telegram trả về phản hồi không hợp lệ") from None

    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError("Telegram API từ chối gửi tin nhắn")
    log.info("Đã gửi cảnh báo Telegram cho %s", unit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", required=True, help="Tên systemd service collector bị lỗi")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        send_telegram_alert(args.unit)
    except RuntimeError as exc:
        log.error("Không gửi được cảnh báo Telegram: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
