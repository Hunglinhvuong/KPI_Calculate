#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV_DIR="${VENV_DIR:-${PROJECT_DIR}/.venv}"
SERVICE_DIR="/etc/systemd/system"
RUNTIME_USER="${RUN_AS_USER:-${SUDO_USER:-}}"
SCHEDULE_TIME="${SCHEDULE_TIME:-07:00:00}"

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this installer with sudo: sudo bash scripts/install_systemd_services.sh" >&2
    exit 1
fi

if [[ -z "${RUNTIME_USER}" || "${RUNTIME_USER}" == "root" ]]; then
    echo "Could not identify the non-root runtime user. Set RUN_AS_USER explicitly." >&2
    exit 1
fi
if ! id "${RUNTIME_USER}" >/dev/null 2>&1; then
    echo "Runtime user does not exist: ${RUNTIME_USER}" >&2
    exit 1
fi
RUNTIME_GROUP="${RUN_AS_GROUP:-$(id -gn "${RUNTIME_USER}")}"
if ! getent group "${RUNTIME_GROUP}" >/dev/null; then
    echo "Runtime group does not exist: ${RUNTIME_GROUP}" >&2
    exit 1
fi
if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    echo "Missing ${PROJECT_DIR}/.env; configure SmartF, PostgreSQL, and proxy settings first." >&2
    exit 1
fi
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Virtualenv Python not found: ${VENV_DIR}/bin/python" >&2
    echo "Create the virtualenv and install requirements as ${RUNTIME_USER} before installing services." >&2
    exit 1
fi
if [[ ! -d "${PROJECT_DIR}/database" || ! -r "${PROJECT_DIR}/.env" ]]; then
    echo "Project files or .env are not accessible by ${RUNTIME_USER}." >&2
    exit 1
fi
if ! runuser -u "${RUNTIME_USER}" -- test -x "${VENV_DIR}/bin/python" \
    || ! runuser -u "${RUNTIME_USER}" -- test -r "${PROJECT_DIR}/.env"; then
    echo "The runtime user ${RUNTIME_USER} cannot execute the virtualenv or read .env." >&2
    exit 1
fi
if [[ ! "${SCHEDULE_TIME}" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$ ]]; then
    echo "SCHEDULE_TIME must use 24-hour HH:MM:SS format (got: ${SCHEDULE_TIME})." >&2
    exit 1
fi

mkdir -p "${PROJECT_DIR}/logs"
chown "${RUNTIME_USER}:${RUNTIME_GROUP}" "${PROJECT_DIR}/logs"

cat > "${SERVICE_DIR}/kpi-ward-day.service" <<EOF
[Unit]
Description=KPI Ward Day collector
Wants=network-online.target
After=network-online.target

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
Wants=network-online.target
After=network-online.target

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

chmod 0644 \
    "${SERVICE_DIR}/kpi-ward-day.service" \
    "${SERVICE_DIR}/kpi-ward-day.timer" \
    "${SERVICE_DIR}/kpi-vhkt.service" \
    "${SERVICE_DIR}/kpi-vhkt.timer"

systemd-analyze verify \
    "${SERVICE_DIR}/kpi-ward-day.service" \
    "${SERVICE_DIR}/kpi-ward-day.timer" \
    "${SERVICE_DIR}/kpi-vhkt.service" \
    "${SERVICE_DIR}/kpi-vhkt.timer"
systemctl daemon-reload
systemctl enable kpi-ward-day.timer kpi-vhkt.timer
systemctl restart kpi-ward-day.timer kpi-vhkt.timer

printf 'Installed system services for user %s (group %s), scheduled at %s.\n' \
    "${RUNTIME_USER}" "${RUNTIME_GROUP}" "${SCHEDULE_TIME}"
printf 'Check timers: sudo systemctl list-timers kpi-ward-day.timer kpi-vhkt.timer\n'
printf 'Check logs:   sudo journalctl -u kpi-ward-day.service -u kpi-vhkt.service -f\n'
