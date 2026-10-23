#!/usr/bin/env bash
# deploy_zoid6.sh -- reinstall the bed, bbsengine6 and letteredolive wheels into the
# zoid6 service venv and the shell/owner venv, run the owner install against the
# zoid6 database, then print the restart and verification steps.
#
# Run as the 'jam' user (the owner role). Do NOT run this with sudo: the systemctl
# restart is printed for you to run, because the install venvs are jam's.
#
# Usage:
#   scripts/deploy_zoid6.sh                 # uses the default wheel names below
#   scripts/deploy_zoid6.sh --wheels DIR    # wheels live in DIR instead
#   scripts/deploy_zoid6.sh --dry-run       # print the commands, change nothing
#
# Exits on the first failed step. Never creates a database: the owner install
# targets 'zoid6', which must already exist.

set -euo pipefail

WHEELS_DIR="/home/opencode/data/work/dist-wheels"
SERVICE_PY="/var/lib/bed/venv/bin/python"
SHELL_PY="/home/jam/.venv/bin/python"
SERVICE_BED="/var/lib/bed/venv/bin/bed"
DB="zoid6"
CONFIG="/etc/zoid6/zoid6.json"
ROUTER="zoid6.api.handler.MessageRouter"

BED_WHEEL="bed-0.0.2.dev20261023173910-py3-none-any.whl"
BBS_WHEEL="bbsengine6-0.0.1.dev202610182201-py3-none-any.whl"
LO_WHEEL="letteredolive-0.0.2.dev20261023192121-py3-none-any.whl"

DRY_RUN=0

usage() {
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --wheels)   WHEELS_DIR="${2:?--wheels needs a directory}"; shift 2 ;;
        --dry-run)  DRY_RUN=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$(id -un)" = "opencode" ]; then
    echo "refusing to run as 'opencode': the installs and owner run must be done as 'jam'." >&2
    exit 1
fi

# Every wheel and interpreter must exist before anything is installed.
for w in "$BED_WHEEL" "$BBS_WHEEL" "$LO_WHEEL"; do
    if [ ! -f "$WHEELS_DIR/$w" ]; then
        echo "missing wheel: $WHEELS_DIR/$w" >&2
        exit 1
    fi
done
for py in "$SERVICE_PY" "$SHELL_PY"; do
    if [ ! -x "$py" ]; then
        echo "missing interpreter: $py" >&2
        exit 1
    fi
done

BED_PATH="$WHEELS_DIR/$BED_WHEEL"
BBS_PATH="$WHEELS_DIR/$BBS_WHEEL"
LO_PATH="$WHEELS_DIR/$LO_WHEEL"

run() {
    echo "+ $*"
    if [ "$DRY_RUN" -eq 0 ]; then
        "$@"
    fi
}

echo "== 1/4 service venv: bed + bbsengine6"
run "$SERVICE_PY" -m pip install --force-reinstall --no-deps "$BED_PATH" "$BBS_PATH"

echo "== 2/4 shell/owner venv: bed + bbsengine6 + letteredolive"
run "$SHELL_PY" -m pip install --force-reinstall --no-deps "$BED_PATH" "$BBS_PATH" "$LO_PATH"

echo "== 3/4 owner install against '$DB' (no database is created by this step)"
run "$SERVICE_BED" --install --databasename "$DB" --config "$CONFIG" --router "$ROUTER"

echo "== 4/4 restart and verify (run these yourself; sudo is not available to this script)"
cat <<EOF
  sudo systemctl restart zoid6
  shell
  psql -d $DB -c "\\df engine.bed_count_unread"
  psql -l
EOF

if [ "$DRY_RUN" -eq 1 ]; then
    echo "(dry run: no commands were executed)"
fi
