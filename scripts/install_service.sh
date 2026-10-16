#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON="${PYTHON:-python3}"

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is required to install the user services." >&2
    exit 1
fi
if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "The systemd user manager is unavailable. Enable user services for this account first." >&2
    exit 1
fi
if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    echo "Created ${PROJECT_DIR}/.env from .env.example; edit it before relying on the dashboard."
    echo "Set STREAMLIT_PORT (8502-8505), PostgreSQL, SmartF and admin credentials in .env, then rerun this installer."
    exit 0
fi

STREAMLIT_PORT="${STREAMLIT_PORT:-$(sed -n 's/^STREAMLIT_PORT=//p' "${PROJECT_DIR}/.env" | tail -n 1)}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8502}"
case "${STREAMLIT_PORT}" in
    8502|8503|8504|8505) ;;
    *)
        echo "STREAMLIT_PORT must be one of 8502, 8503, 8504, or 8505 (got: ${STREAMLIT_PORT})." >&2
        exit 1
        ;;
esac

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    "${PYTHON}" -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r "${PROJECT_DIR}/requirements.txt"

# Keep the Playwright browser binary with this project's virtual environment.
PLAYWRIGHT_BROWSERS_PATH=0 "${VENV_DIR}/bin/python" -m playwright install chromium

python3 "${PROJECT_DIR}/scripts/write_service_units.py" "${PROJECT_DIR}" "${VENV_DIR}" "${STREAMLIT_PORT}"

for unit in kpi-dashboard.service kpi-monthly-cleanup.service kpi-monthly-cleanup.timer; do
    if ! systemctl --user is-enabled "${unit}" >/dev/null 2>&1; then
        systemctl --user link "${PROJECT_DIR}/${unit}"
    fi
done
systemctl --user daemon-reload
systemctl --user enable --now kpi-dashboard.service kpi-monthly-cleanup.timer
systemctl --user restart kpi-dashboard.service

echo "Installed project-local units. The systemd unit links point into ${PROJECT_DIR}; no unit files were copied elsewhere."
echo "Dashboard: http://<server-address>:${STREAMLIT_PORT}"
