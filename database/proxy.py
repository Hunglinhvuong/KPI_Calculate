"""Build Playwright proxy options from standard proxy environment variables."""
from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit


def playwright_proxy_from_env() -> dict[str, str] | None:
    proxy_value = next(
        (
            os.environ[name].strip()
            for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
            if os.environ.get(name, "").strip()
        ),
        None,
    )
    if proxy_value is None:
        return None

    proxy_url = proxy_value if "://" in proxy_value else f"http://{proxy_value}"
    parsed = urlsplit(proxy_url)
    if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
        raise ValueError("Proxy phải có dạng http(s)://host:port hoặc socks5://host:port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Proxy URL không được chứa path, query hoặc fragment")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Proxy có cổng không hợp lệ") from exc
    if port is None:
        raise ValueError("Proxy URL phải chỉ định cổng, ví dụ http://proxy.example:8080")

    hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    server = f"{parsed.scheme}://{hostname}:{port}"
    options = {"server": server}
    if parsed.username is not None:
        options["username"] = unquote(parsed.username)
    if parsed.password is not None:
        options["password"] = unquote(parsed.password)

    bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if bypass and bypass.strip():
        options["bypass"] = bypass.strip()
    return options
