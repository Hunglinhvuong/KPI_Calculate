#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "The systemd user manager is unavailable; no services were changed." >&2
    exit 1
fi

systemctl --user disable --now kpi-dashboard.service kpi-monthly-cleanup.timer

UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
for unit in kpi-dashboard.service kpi-monthly-cleanup.service kpi-monthly-cleanup.timer; do
    unit_link="${UNIT_DIR}/${unit}"
    if [[ -L "${unit_link}" ]] && [[ "$(readlink -f -- "${unit_link}")" == "${PROJECT_DIR}/${unit}" ]]; then
        rm -- "${unit_link}"
    fi
done

systemctl --user daemon-reload

echo "Disabled the project services. Project-local .service/.timer files and application data were left untouched."
