#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV_DIR="${VENV_DIR:-${PROJECT_DIR}/.venv}"
SERVICE_DIR="/etc/systemd/system"
RUNTIME_USER="${RUN_AS_USER:-$(id -un)}"
RUNTIME_GROUP="${RUN_AS_GROUP:-$(id -gn)}"
SCHEDULE_TIME="${SCHEDULE_TIME:-07:00:00}"

if [[ ${EUID} -ne 0 ]]; then
    echo "This installer must run as root. Use: sudo bash scripts/install_systemd_services.sh" >&2
    exit 1
fi

if [[ ! -d "${PROJECT_DIR}" ]]; then
    echo "Project directory not found: ${PROJECT_DIR}" >&2
    exit 1
fi

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    echo "Missing ${PROJECT_DIR}/.env; copy .env.example and configure SmartF/PostgreSQL settings first." >&2
    exit 1
fi

mkdir -p "${PROJECT_DIR}/logs"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Creating virtual environment at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install --upgrade pip >/dev/null
"${VENV_DIR}/bin/python" -m pip install -r "${PROJECT_DIR}/requirements.txt" >/dev/null
PLAYWRIGHT_BROWSERS_PATH=0 "${VENV_DIR}/bin/python" -m playwright install chromium >/dev/null

cat > "${SERVICE_DIR}/kpi-ward-day.service" <<EOF
[Unit]
Description=KPI Ward Day collector
After=network.target

[Service]
Type=oneshot
User=${RUNTIME_USER}
Group=${RUNTIME_GROUP}
WorkingDirectory=${PROJECT_DIR}
Environment=PLAYWRIGHT_BROWSERS_PATH=0
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${VENV_DIR}/bin/python -m database.collector_ward_day

[Install]
WantedBy=multi-user.target
EOF

cat > "${SERVICE_DIR}/kpi-ward-day.timer" <<EOF
[Unit]
Description=Run KPI Ward Day collector every day at ${SCHEDULE_TIME}

[Timer]
OnCalendar=*-*-* ${SCHEDULE_TIME}
Persistent=true
Unit=kpi-ward-day.service

[Install]
WantedBy=timers.target
EOF

cat > "${SERVICE_DIR}/kpi-vhkt.service" <<EOF
[Unit]
Description=KPI VHKT collector
After=network.target

[Service]
Type=oneshot
User=${RUNTIME_USER}
Group=${RUNTIME_GROUP}
WorkingDirectory=${PROJECT_DIR}
Environment=PLAYWRIGHT_BROWSERS_PATH=0
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${VENV_DIR}/bin/python -m database.collector_vhkt

[Install]
WantedBy=multi-user.target
EOF

cat > "${SERVICE_DIR}/kpi-vhkt.timer" <<EOF
[Unit]
Description=Run KPI VHKT collector every day at ${SCHEDULE_TIME}

[Timer]
OnCalendar=*-*-* ${SCHEDULE_TIME}
Persistent=true
Unit=kpi-vhkt.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now kpi-ward-day.timer kpi-vhkt.timer

systemctl --no-pager status kpi-ward-day.timer kpi-vhkt.timer --lines=20 || true

echo
printf 'Installed systemd timers:\n'
printf '  - %s\n' "${SERVICE_DIR}/kpi-ward-day.service" "${SERVICE_DIR}/kpi-ward-day.timer" "${SERVICE_DIR}/kpi-vhkt.service" "${SERVICE_DIR}/kpi-vhkt.timer"
printf 'They will run at %s every day.\n' "${SCHEDULE_TIME}"
printf 'Check logs with:\n'
printf '  sudo journalctl -u kpi-ward-day.service -f\n'
printf '  sudo journalctl -u kpi-vhkt.service -f\n'
