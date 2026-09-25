#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="klipper-activated_carbon_monitor"
UPDATE_MANAGER_NAME="activated-carbon-monitor"
MODULE_NAME="activated_carbon_monitor.py"
DEFAULT_KLIPPER_DIR="${HOME}/klipper"
DEFAULT_MOONRAKER_DIR="${HOME}/moonraker"
DEFAULT_MOONRAKER_CONF="${HOME}/printer_data/config/moonraker.conf"

KLIPPER_DIR="${KLIPPER_DIR:-$DEFAULT_KLIPPER_DIR}"
MOONRAKER_DIR="${MOONRAKER_DIR:-$DEFAULT_MOONRAKER_DIR}"
MOONRAKER_CONF="${MOONRAKER_CONF:-$DEFAULT_MOONRAKER_CONF}"

usage() {
    cat <<USAGE
Usage: $0 [-k KLIPPER_DIR] [-r MOONRAKER_DIR] [-m MOONRAKER_CONF]

Installs ${PROJECT_NAME} as a Klipper extra and Moonraker companion,
then registers the repository with Moonraker's update manager.

Options:
  -k DIR   Klipper source directory (default: ~/klipper)
  -r DIR   Moonraker source directory (default: ~/moonraker)
  -m FILE  moonraker.conf path (default: ~/printer_data/config/moonraker.conf)
  -h       Show this help
USAGE
}

while getopts ":k:r:m:h" opt; do
    case "$opt" in
        k) KLIPPER_DIR="$OPTARG" ;;
        r) MOONRAKER_DIR="$OPTARG" ;;
        m) MOONRAKER_CONF="$OPTARG" ;;
        h) usage; exit 0 ;;
        :) echo "Option -$OPTARG requires an argument" >&2; exit 2 ;;
        \?) echo "Unknown option: -$OPTARG" >&2; usage; exit 2 ;;
    esac
done

if [[ ${EUID} -eq 0 ]]; then
    echo "Do not run this installer as root. Run it as the same user that owns Klipper and Moonraker." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

KLIPPER_SOURCE="${REPO_DIR}/klippy/extras/${MODULE_NAME}"
KLIPPER_EXTRAS="${KLIPPER_DIR}/klippy/extras"
KLIPPER_TARGET="${KLIPPER_EXTRAS}/${MODULE_NAME}"

MOONRAKER_SOURCE="${REPO_DIR}/moonraker/components/${MODULE_NAME}"
MOONRAKER_COMPONENTS="${MOONRAKER_DIR}/moonraker/components"
MOONRAKER_TARGET="${MOONRAKER_COMPONENTS}/${MODULE_NAME}"

MOONRAKER_CONFIG_DIR="$(cd "$(dirname "$MOONRAKER_CONF")" && pwd)"
MANAGER_CONF="${MOONRAKER_CONFIG_DIR}/${PROJECT_NAME}.conf"
INCLUDE_LINE="[include ${PROJECT_NAME}.conf]"

if [[ ! -d "${REPO_DIR}/.git" ]]; then
    echo "${REPO_DIR} is not a git checkout." >&2
    echo "Clone the repository with git, then run ./install.sh from inside it." >&2
    exit 1
fi

if [[ ! -f "$KLIPPER_SOURCE" ]]; then
    echo "Klipper plugin module not found: $KLIPPER_SOURCE" >&2
    exit 1
fi

if [[ ! -f "$MOONRAKER_SOURCE" ]]; then
    echo "Moonraker companion module not found: $MOONRAKER_SOURCE" >&2
    exit 1
fi

if [[ ! -d "$KLIPPER_EXTRAS" ]]; then
    echo "Klipper extras directory not found: $KLIPPER_EXTRAS" >&2
    echo "Use -k to specify the Klipper source directory." >&2
    exit 1
fi

if [[ ! -d "$MOONRAKER_COMPONENTS" ]]; then
    echo "Moonraker components directory not found: $MOONRAKER_COMPONENTS" >&2
    echo "Use -r to specify the Moonraker source directory." >&2
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

link_module() {
    local source="$1"
    local target="$2"
    local label="$3"

    if [[ -e "$target" && ! -L "$target" ]]; then
        local backup="${target}.backup.$(date +%Y%m%d%H%M%S)"
        echo "Existing non-symlink ${label} module found; moving it to: $backup"
        mv "$target" "$backup"
    fi

    ln -sfn "$source" "$target"
    echo "Linked ${label}: $target -> $source"
}

printf 'Linking plugin modules...\n'
link_module "$KLIPPER_SOURCE" "$KLIPPER_TARGET" "Klipper"
link_module "$MOONRAKER_SOURCE" "$MOONRAKER_TARGET" "Moonraker"

printf 'Registering Moonraker component and update manager...\n'
cat > "$MANAGER_CONF" <<EOF_MANAGER
# Managed by ${PROJECT_NAME}/install.sh

[activated_carbon_monitor]
sensor_name: activated_carbon
friendly_name: Activated Carbon

[update_manager ${UPDATE_MANAGER_NAME}]
type: git_repo
channel: dev
path: ${REPO_DIR}
origin: ${ORIGIN}
primary_branch: ${BRANCH}
managed_services: klipper moonraker
info_tags:
    desc=Klipper Activated Carbon Monitor
EOF_MANAGER

if ! grep -Fqx "$INCLUDE_LINE" "$MOONRAKER_CONF"; then
    BACKUP="${MOONRAKER_CONF}.backup.$(date +%Y%m%d%H%M%S)"
    cp "$MOONRAKER_CONF" "$BACKUP"
    printf '\n# Klipper Activated Carbon Monitor\n%s\n' "$INCLUDE_LINE" >> "$MOONRAKER_CONF"
    echo "Backed up moonraker.conf to: $BACKUP"
fi

service_exists() {
    local service="$1"
    command -v systemctl >/dev/null 2>&1 &&
        systemctl list-unit-files "${service}.service" --no-legend 2>/dev/null |
        grep -q "${service}.service"
}

prompt_restart() {
    local service="$1"
    local warning="$2"

    if ! service_exists "$service"; then
        return
    fi

    if [[ ! -t 0 ]]; then
        echo "Not restarting ${service}: installer is running non-interactively."
        return
    fi

    echo
    if [[ -n "$warning" ]]; then
        echo "$warning"
    fi
    read -r -p "Restart ${service} now? [y/N] " reply
    case "$reply" in
        y|Y|yes|YES|Yes)
            echo "Restarting ${service}..."
            sudo systemctl restart "$service"
            ;;
        *)
            echo "Leaving ${service} running."
            ;;
    esac
}

echo
echo "Installation files are in place."
echo "No services are restarted automatically."

prompt_restart moonraker "Moonraker must be restarted before its dashboard component and update-manager entry are loaded."
prompt_restart klipper "WARNING: Restarting Klipper will immediately stop any active print. Only restart when the printer is idle."

echo
echo "Installation complete."
echo "Klipper module:   $KLIPPER_TARGET -> $KLIPPER_SOURCE"
echo "Moonraker module: $MOONRAKER_TARGET -> $MOONRAKER_SOURCE"
echo "Moonraker config: $MANAGER_CONF"
echo
echo "Next, add the monitor section to printer.cfg, for example:"
echo
echo "[activated_carbon_monitor chamber]"
echo "fans: hepa_left, hepa_right"
echo
echo "After Klipper is ready, Mainsail 2.12+ will show an Activated Carbon"
echo "sensor in the Miscellaneous dashboard panel."
