"""Write systemd user units into the project directory (never install/copy them)."""
from __future__ import annotations

from pathlib import Path
import sys


def _unit_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "$$")
        .replace("%", "%%")
    )


def _unit_path(value: str) -> str:
    return value.replace("\\", "\\x5c").replace(" ", "\\x20").replace("%", "%%")


def write_units(project_dir: Path, venv_dir: Path, port: int = 8502) -> list[Path]:
    if port not in {8502, 8503, 8504, 8505}:
        raise ValueError("Streamlit port must be one of: 8502, 8503, 8504, 8505")

    project_dir = project_dir.resolve()
    venv_dir = venv_dir.resolve()
    working_directory = _unit_path(str(project_dir))
    venv = _unit_escape(str(venv_dir))

    units = {
        "kpi-dashboard.service": f"""[Unit]
Description=KPI Dashboard Streamlit application
After=network.target

[Service]
Type=simple
WorkingDirectory={working_directory}
Environment=PLAYWRIGHT_BROWSERS_PATH=0
ExecStart="{venv}/bin/python" -m streamlit run app.py --server.headless=true --server.address=0.0.0.0 --server.port={port}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""",
        "kpi-monthly-cleanup.service": f"""[Unit]
Description=Archive monthly KPI and clean old source rows
After=network.target

[Service]
Type=oneshot
WorkingDirectory={working_directory}
ExecStart="{venv}/bin/python" -m jobs.jobs cleanup
""",
        "kpi-monthly-cleanup.timer": """[Unit]
Description=Run KPI source cleanup on the first day of each month

[Timer]
OnCalendar=*-*-01 02:05:00
Persistent=true
Unit=kpi-monthly-cleanup.service

[Install]
WantedBy=timers.target
""",
    }
    output_paths = []
    for name, contents in units.items():
        path = project_dir / name
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o644)
        output_paths.append(path)
    return output_paths


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) not in {2, 3}:
        print("Usage: write_service_units.py PROJECT_DIR VENV_DIR [STREAMLIT_PORT]", file=sys.stderr)
        return 2
    try:
        port = int(args[2]) if len(args) == 3 else 8502
        paths = write_units(Path(args[0]), Path(args[1]), port)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    for path in paths:
        print(f"Wrote project-local unit: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
