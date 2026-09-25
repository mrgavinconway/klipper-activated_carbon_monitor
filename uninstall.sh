#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="klipper-activated_carbon_monitor"
MODULE_NAME="activated_carbon_monitor.py"
DEFAULT_KLIPPER_DIR="${HOME}/klipper"
DEFAULT_MOONRAKER_CONF="${HOME}/printer_data/config/moonraker.conf"

KLIPPER_DIR="${KLIPPER_DIR:-$DEFAULT_KLIPPER_DIR}"
MOONRAKER_CONF="${MOONRAKER_CONF:-$DEFAULT_MOONRAKER_CONF}"

while getopts ":k:m:h" opt; do
    case "$opt" in
        k) KLIPPER_DIR="$OPTARG" ;;
        m) MOONRAKER_CONF="$OPTARG" ;;
        h) echo "Usage: $0 [-k KLIPPER_DIR] [-m MOONRAKER_CONF]"; exit 0 ;;
        :) echo "Option -$OPTARG requires an argument" >&2; exit 2 ;;
        \?) echo "Unknown option: -$OPTARG" >&2; exit 2 ;;
    esac
done

if [[ ${EUID} -eq 0 ]]; then
    echo "Do not run this uninstaller as root." >&2
    exit 1
fi

TARGET_FILE="${KLIPPER_DIR}/klippy/extras/${MODULE_NAME}"
MOONRAKER_CONFIG_DIR="$(cd "$(dirname "$MOONRAKER_CONF")" && pwd)"
MANAGER_CONF="${MOONRAKER_CONFIG_DIR}/${PROJECT_NAME}.conf"
INCLUDE_LINE="[include ${PROJECT_NAME}.conf]"

if [[ -L "$TARGET_FILE" ]]; then
    rm "$TARGET_FILE"
    echo "Removed Klipper module symlink: $TARGET_FILE"
else
    echo "No plugin symlink found at: $TARGET_FILE"
fi

rm -f "$MANAGER_CONF"

if [[ -f "$MOONRAKER_CONF" ]]; then
    python3 - "$MOONRAKER_CONF" "$INCLUDE_LINE" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
include = sys.argv[2]
lines = path.read_text().splitlines()
out = []
skip_comment = False
for line in lines:
    if line.strip() == "# Klipper Activated Carbon Monitor":
        skip_comment = True
        continue
    if line.strip() == include:
        skip_comment = False
        continue
    if skip_comment and not line.strip():
        continue
    skip_comment = False
    out.append(line)
path.write_text("\n".join(out).rstrip() + "\n")
PY
fi

echo "Moonraker update-manager registration removed."
echo "Repository and persistent carbon-history data were left in place."
echo "Remove the [activated_carbon_monitor ...] section from printer.cfg before restarting Klipper."
