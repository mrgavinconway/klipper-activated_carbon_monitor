#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="klipper-activated_carbon_monitor"
UPDATE_MANAGER_NAME="activated-carbon-monitor"
MODULE_NAME="activated_carbon_monitor.py"
DEFAULT_KLIPPER_DIR="${HOME}/klipper"
DEFAULT_MOONRAKER_CONF="${HOME}/printer_data/config/moonraker.conf"

KLIPPER_DIR="${KLIPPER_DIR:-$DEFAULT_KLIPPER_DIR}"
MOONRAKER_CONF="${MOONRAKER_CONF:-$DEFAULT_MOONRAKER_CONF}"

usage() {
    cat <<USAGE
Usage: $0 [-k KLIPPER_DIR] [-m MOONRAKER_CONF]

Installs ${PROJECT_NAME} as a Klipper extra and registers the repository
with Moonraker's update manager.

Options:
  -k DIR   Klipper source directory (default: ~/klipper)
  -m FILE  moonraker.conf path (default: ~/printer_data/config/moonraker.conf)
  -h       Show this help
USAGE
}

while getopts ":k:m:h" opt; do
    case "$opt" in
        k) KLIPPER_DIR="$OPTARG" ;;
        m) MOONRAKER_CONF="$OPTARG" ;;
        h) usage; exit 0 ;;
        :) echo "Option -$OPTARG requires an argument" >&2; exit 2 ;;
        \?) echo "Unknown option: -$OPTARG" >&2; usage; exit 2 ;;
    esac
done

if [[ ${EUID} -eq 0 ]]; then
    echo "Do not run this installer as root. Run it as the same user that owns Klipper." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
SOURCE_FILE="${REPO_DIR}/klippy/extras/${MODULE_NAME}"
KLIPPER_EXTRAS="${KLIPPER_DIR}/klippy/extras"
TARGET_FILE="${KLIPPER_EXTRAS}/${MODULE_NAME}"
MOONRAKER_CONFIG_DIR="$(cd "$(dirname "$MOONRAKER_CONF")" && pwd)"
MANAGER_CONF="${MOONRAKER_CONFIG_DIR}/${PROJECT_NAME}.conf"
INCLUDE_LINE="[include ${PROJECT_NAME}.conf]"

if [[ ! -d "${REPO_DIR}/.git" ]]; then
    echo "${REPO_DIR} is not a git checkout." >&2
    echo "Clone the repository with git, then run ./install.sh from inside it." >&2
    exit 1
fi

if [[ ! -f "$SOURCE_FILE" ]]; then
    echo "Plugin module not found: $SOURCE_FILE" >&2
    exit 1
fi

if [[ ! -d "$KLIPPER_EXTRAS" ]]; then
    echo "Klipper extras directory not found: $KLIPPER_EXTRAS" >&2
    echo "Use -k to specify the Klipper source directory." >&2
    exit 1
fi

if [[ ! -f "$MOONRAKER_CONF" ]]; then
    echo "Moonraker config not found: $MOONRAKER_CONF" >&2
    echo "Use -m to specify moonraker.conf." >&2
    exit 1
fi

ORIGIN="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
if [[ -z "$ORIGIN" ]]; then
    echo "The repository has no 'origin' remote; Moonraker cannot manage updates." >&2
    exit 1
fi

BRANCH="$(git -C "$REPO_DIR" symbolic-ref --short HEAD 2>/dev/null || true)"
if [[ -z "$BRANCH" ]]; then
    BRANCH="main"
fi

printf 'Linking Klipper module...\n'
if [[ -e "$TARGET_FILE" && ! -L "$TARGET_FILE" ]]; then
    BACKUP="${TARGET_FILE}.backup.$(date +%Y%m%d%H%M%S)"
    echo "Existing non-symlink module found; moving it to: $BACKUP"
    mv "$TARGET_FILE" "$BACKUP"
fi
ln -sfn "$SOURCE_FILE" "$TARGET_FILE"

printf 'Registering Moonraker update manager...\n'
cat > "$MANAGER_CONF" <<EOF_MANAGER
# Managed by ${PROJECT_NAME}/install.sh
[update_manager ${UPDATE_MANAGER_NAME}]
type: git_repo
channel: dev
path: ${REPO_DIR}
origin: ${ORIGIN}
primary_branch: ${BRANCH}
managed_services: klipper
info_tags:
    desc=Klipper Activated Carbon Monitor
EOF_MANAGER

if ! grep -Fqx "$INCLUDE_LINE" "$MOONRAKER_CONF"; then
    BACKUP="${MOONRAKER_CONF}.backup.$(date +%Y%m%d%H%M%S)"
    cp "$MOONRAKER_CONF" "$BACKUP"
    printf '\n# Klipper Activated Carbon Monitor\n%s\n' "$INCLUDE_LINE" >> "$MOONRAKER_CONF"
    echo "Backed up moonraker.conf to: $BACKUP"
fi

restart_if_present() {
    local service="$1"
    if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files "${service}.service" --no-legend 2>/dev/null | grep -q "${service}.service"; then
        echo "Restarting ${service}..."
        sudo systemctl restart "$service"
    fi
}

restart_if_present moonraker
restart_if_present klipper

echo
echo "Installation complete."
echo "Klipper module: $TARGET_FILE -> $SOURCE_FILE"
echo "Moonraker update config: $MANAGER_CONF"
echo
echo "Next, add the monitor section to printer.cfg, for example:"
echo
echo "[activated_carbon_monitor chamber]"
echo "fans: fan_generic voron_aire_left; fan_generic voron_aire_right"
