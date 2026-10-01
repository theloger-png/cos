#!/usr/bin/env bash
#
# cos-install.sh - install, update, back up and restore COS.
#
# This script is idempotent: it is safe to re-run on an already-installed
# controller or agent machine. A re-run also doubles as the update path -
# it pulls the latest code, reinstalls the COS package, rebuilds the portal
# (controller) and restarts the service, without touching secrets, the
# database, or any user-edited config.
#
# Usage:
#   cos-install.sh --role controller|agent [options]
#
# Options common to both roles:
#   --yes                          Assume "yes" to any confirmation prompt
#
# Agent-only options (for non-interactive install/update):
#   --controller-url URL           Controller base URL, e.g. http://10.0.0.2:8090
#   --controller-api-key KEY       Controller API key
#   (or set COS_INSTALL_CONTROLLER_URL / COS_INSTALL_CONTROLLER_API_KEY)
#   These are only used the first time (when /opt/cos/config/agent.env does
#   not exist yet); on every later run the existing agent.env is left alone.
#
# Controller-only backup/restore:
#   --backup [FILE]                Write a backup of the DB + secrets + config
#                                   to FILE (default ~/cos-backup-<timestamp>.tar.gz)
#                                   and exit without installing anything.
#   --restore FILE                 Run a normal controller install, then
#                                   restore the DB + secrets + config from FILE.
#                                   Prompts for confirmation unless --yes.
#
# Non-negotiable rule: a re-run NEVER regenerates or overwrites secrets,
# credentials, user data or user-edited config. The only exception is an
# explicit --restore, whose entire purpose is to replace them from a backup.
#
set -euo pipefail
set -E  # inherit the ERR trap into functions and subshells

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)

ROLE=""
ASSUME_YES=0
BACKUP_MODE=0
BACKUP_FILE=""
RESTORE_MODE=0
RESTORE_FILE=""
CONTROLLER_URL="${COS_INSTALL_CONTROLLER_URL:-}"
CONTROLLER_API_KEY="${COS_INSTALL_CONTROLLER_API_KEY:-}"

STEP_NUM=0
STEP_TOTAL=0
CURRENT_STEP="argument parsing"

usage() {
    sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

die() {
    echo "Error: $*" >&2
    exit 1
}

on_err() {
    local exit_code=$?
    echo "" >&2
    echo "==> FAILED at step ${STEP_NUM}/${STEP_TOTAL}: ${CURRENT_STEP} (exit code ${exit_code})" >&2
    echo "==> See the output above for details. It is safe to fix the issue and re-run this script." >&2
}
trap on_err ERR

step() {
    STEP_NUM=$((STEP_NUM + 1))
    CURRENT_STEP="$*"
    echo ""
    echo "[${STEP_NUM}/${STEP_TOTAL}] $*"
}

# --- Argument parsing --------------------------------------------------------

# Keep a copy of the original arguments: the parsing loop below consumes "$@"
# with shift, and the git-update step needs them to re-exec this script.
ORIG_ARGS=("$@")

while [[ $# -gt 0 ]]; do
    case "$1" in
        --role)
            ROLE="${2:-}"
            [[ -n "$ROLE" ]] || usage
            shift 2
            ;;
        --backup)
            BACKUP_MODE=1
            if [[ $# -ge 2 && "$2" != --* ]]; then
                BACKUP_FILE="$2"
                shift 2
            else
                shift 1
            fi
            ;;
        --restore)
            RESTORE_MODE=1
            RESTORE_FILE="${2:-}"
            [[ -n "$RESTORE_FILE" ]] || usage
            shift 2
            ;;
        --yes)
            ASSUME_YES=1
            shift
            ;;
        --controller-url)
            CONTROLLER_URL="${2:-}"
            shift 2
            ;;
        --controller-api-key)
            CONTROLLER_API_KEY="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            usage
            ;;
    esac
done

[[ "$ROLE" == "controller" || "$ROLE" == "agent" ]] || usage
[[ "$BACKUP_MODE" -eq 1 && "$ROLE" != "controller" ]] && die "--backup is only supported with --role controller"
[[ "$RESTORE_MODE" -eq 1 && "$ROLE" != "controller" ]] && die "--restore is only supported with --role controller"
[[ "$BACKUP_MODE" -eq 1 && "$RESTORE_MODE" -eq 1 ]] && die "--backup and --restore cannot be used together"

if [[ "$BACKUP_MODE" -eq 1 ]]; then
    STEP_TOTAL=4
elif [[ "$ROLE" == "controller" ]]; then
    STEP_TOTAL=22
    [[ "$RESTORE_MODE" -eq 1 ]] && STEP_TOTAL=29
else
    STEP_TOTAL=18
fi

# --- Helpers shared by multiple sections -------------------------------------

# Prints "1" if the controller answers on 127.0.0.1:8090 within ~30s, else "0".
wait_for_controller() {
    local i
    for i in $(seq 1 30); do
        if curl -fsS -o /dev/null "http://127.0.0.1:8090/openapi.json" 2>/dev/null; then
            echo 1
            return
        fi
        sleep 1
    done
    echo 0
}

# Installs $2 (a rendered unit file) as $1 only if content differs, and
# daemon-reloads when it changes. Always safe to call on every run.
install_unit_if_changed() {
    local unit_path="$1" tmp_file="$2"
    if [[ -f "$unit_path" ]] && cmp -s "$tmp_file" "$unit_path"; then
        echo "  Unit file unchanged: $unit_path"
    else
        cp "$tmp_file" "$unit_path"
        systemctl daemon-reload
        echo "  Unit file installed/updated: $unit_path"
    fi
}

# --- Backup mode (controller only, exits without installing) ----------------

do_backup() {
    step "Checking backup prerequisites"
    [[ "$(id -u)" -eq 0 ]] || die "must be run as root"
    command -v tar >/dev/null 2>&1 || die "tar not found"
    systemctl is-active --quiet postgresql || die "postgresql is not running - cannot pg_dump"
    [[ -d /opt/cos ]] || die "/opt/cos not found - is this a controller machine?"

    local backup_user backup_home out tmpdir
    backup_user="${SUDO_USER:-root}"
    backup_home=$(getent passwd "$backup_user" | cut -d: -f6)
    [[ -n "$backup_home" ]] || backup_home="$HOME"

    if [[ -n "$BACKUP_FILE" ]]; then
        out="$BACKUP_FILE"
    else
        out="${backup_home}/cos-backup-$(date +%Y%m%d-%H%M).tar.gz"
    fi

    tmpdir=$(mktemp -d)

    step "Dumping the cos database"
    sudo -u postgres pg_dump cos > "${tmpdir}/cos.sql" || die "pg_dump failed"

    step "Collecting secrets and configuration"
    for f in /opt/cos/admin_api_key /opt/cos/admin_password; do
        [[ -f "$f" ]] && cp -p "$f" "${tmpdir}/$(basename "$f")"
    done
    if [[ -d /opt/cos/config ]]; then
        cp -rp /opt/cos/config "${tmpdir}/config"
    fi

    step "Writing backup archive"
    tar -czf "$out" -C "$tmpdir" .
    rm -rf "$tmpdir"
    chmod 600 "$out"
    if [[ -n "${SUDO_USER:-}" ]]; then
        chown "$backup_user":"$(id -gn "$backup_user")" "$out" 2>/dev/null || true
    fi

    echo ""
    echo "=== Backup written to: $out (mode 600) ==="
    exit 0
}

if [[ "$BACKUP_MODE" -eq 1 ]]; then
    do_backup
fi

# --- Restore (controller only): applied after the normal install below ------

do_restore() {
    [[ -f "$RESTORE_FILE" ]] || die "backup file not found: $RESTORE_FILE"

    if [[ "$ASSUME_YES" -ne 1 ]]; then
        echo ""
        echo "WARNING: this will DROP and recreate the 'cos' database, replacing"
        echo "all current data with the contents of: $RESTORE_FILE"
        read -r -p "Type 'yes' to continue: " CONFIRM
        [[ "$CONFIRM" == "yes" ]] || die "restore aborted by operator"
    fi

    step "Restoring from backup: $RESTORE_FILE"
    local tmpdir
    tmpdir=$(mktemp -d)
    tar -xzf "$RESTORE_FILE" -C "$tmpdir"
    [[ -f "${tmpdir}/cos.sql" ]] || die "backup archive is missing cos.sql - not a valid COS backup"

    step "Stopping cos-controller for restore"
    systemctl stop cos-controller

    step "Dropping and recreating the cos database"
    sudo -u postgres psql -c "DROP DATABASE IF EXISTS cos"
    sudo -u postgres psql -c "CREATE DATABASE cos OWNER cos"

    step "Loading database dump"
    sudo -u postgres psql -q cos < "${tmpdir}/cos.sql"

    step "Restoring secrets and config from backup"
    if [[ -f "${tmpdir}/admin_api_key" ]]; then
        cp -p "${tmpdir}/admin_api_key" /opt/cos/admin_api_key
        chown cos:cos /opt/cos/admin_api_key
        chmod 640 /opt/cos/admin_api_key
    fi
    if [[ -f "${tmpdir}/admin_password" ]]; then
        cp -p "${tmpdir}/admin_password" /opt/cos/admin_password
        chown cos:cos /opt/cos/admin_password
        chmod 640 /opt/cos/admin_password
    fi
    if [[ -d "${tmpdir}/config" ]]; then
        cp -rp "${tmpdir}/config/." /opt/cos/config/
        chown -R cos:cos /opt/cos/config
    fi
    rm -rf "$tmpdir"

    step "Running alembic upgrade head (post-restore)"
    COS_DATABASE_URL=postgresql+asyncpg://cos:cos@localhost/cos \
        /opt/cos/venv/bin/alembic --config "$REPO_DIR/alembic.ini" upgrade head

    step "Starting cos-controller"
    systemctl start cos-controller
    local ready
    ready=$(wait_for_controller)
    if [[ "$ready" -eq 1 ]]; then
        echo "  [OK] controller answered health check after restore"
    else
        echo "  [FAIL] controller did not answer within 30s after restore - check: journalctl -u cos-controller -n 50" >&2
    fi

    echo ""
    echo "=== Restore from $RESTORE_FILE complete ==="
}

# ===========================================================================
# COMMON (steps 1-9 for both roles)
# ===========================================================================

step "Checking for root privileges"
if [[ "$(id -u)" -ne 0 ]]; then
    die "this script must be run as root (try: sudo $0 ...)"
fi

step "Checking operating system"
if ! grep -q 'Ubuntu 24.04' /etc/os-release 2>/dev/null; then
    die "this script requires Ubuntu 24.04 LTS"
fi

GIT_USER="${SUDO_USER:-root}"

step "Updating source from git"
if [[ -d "$REPO_DIR/.git" ]]; then
    if [[ "${COS_INSTALL_REEXEC:-0}" -eq 1 ]]; then
        echo "  Already re-executed after a git update in this run - skipping a second pull."
    else
        echo "  Discarding local changes to tracked files (e.g. stale .pyc files) as $GIT_USER..."
        sudo -u "$GIT_USER" git -C "$REPO_DIR" checkout -- . \
            || die "git checkout -- . failed in $REPO_DIR"
        BEFORE_SHA=$(sudo -u "$GIT_USER" git -C "$REPO_DIR" rev-parse HEAD)
        echo "  Pulling latest changes (fast-forward only)..."
        if ! sudo -u "$GIT_USER" git -C "$REPO_DIR" pull --ff-only; then
            die "git pull --ff-only failed in $REPO_DIR - resolve manually (diverged branch, local commits, or no network) and re-run"
        fi
        AFTER_SHA=$(sudo -u "$GIT_USER" git -C "$REPO_DIR" rev-parse HEAD)
        if [[ "$BEFORE_SHA" != "$AFTER_SHA" ]]; then
            # bash reads this very file incrementally as it executes it; if the
            # pull just changed it on disk, continuing would run an undefined
            # mix of old and new code. Re-exec the (now-updated) script once,
            # guarded by COS_INSTALL_REEXEC so it cannot loop. Root/OS checks
            # and argument parsing simply run again on the new process, which
            # is harmless.
            echo "  Source updated ($BEFORE_SHA -> $AFTER_SHA) - re-executing the updated script..."
            export COS_INSTALL_REEXEC=1
            exec "$0" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
        fi
    fi
else
    echo "  $REPO_DIR is not a git checkout - skipping update, using the code on disk as-is."
fi

step "Installing common system packages"
apt-get update -q
apt-get install -y -q python3.12 python3.12-venv python3.12-dev git curl wget pkg-config libvirt-dev build-essential

step "Creating cos group and user"
if ! getent group cos > /dev/null 2>&1; then
    groupadd --system cos
fi
if ! id cos > /dev/null 2>&1; then
    useradd --system \
            --gid cos \
            --home-dir /opt/cos \
            --no-create-home \
            --shell /usr/sbin/nologin \
            cos
fi
if [[ -n "${SUDO_USER:-}" ]] && [[ "$SUDO_USER" != "root" ]]; then
    usermod -aG cos "$SUDO_USER"
fi

step "Creating common directories"
install -d -o cos -g cos -m 750 /opt/cos
install -d -o cos -g cos -m 750 /var/log/cos
install -d -o cos -g cos -m 750 /run/cos

step "Creating Python virtual environment"
if [[ -x /opt/cos/venv/bin/python ]]; then
    echo "  /opt/cos/venv already exists - skipping creation."
else
    python3.12 -m venv /opt/cos/venv
    chown -R cos:cos /opt/cos/venv
fi

step "Installing the COS Python package"
/opt/cos/venv/bin/pip install --force-reinstall --no-cache-dir --no-deps "$REPO_DIR/" -q

step "Installing Python dependencies"
/opt/cos/venv/bin/pip install -r "$REPO_DIR/requirements.txt" -q

# ===========================================================================
# CONTROLLER (steps 10-22, +restore steps 23-29 if --restore)
# ===========================================================================

if [[ "$ROLE" == "controller" ]]; then

    ADMIN_KEY_EXISTED=0
    [[ -f /opt/cos/admin_api_key ]] && ADMIN_KEY_EXISTED=1
    ADMIN_PASSWORD_EXISTED=0
    [[ -f /opt/cos/admin_password ]] && ADMIN_PASSWORD_EXISTED=1

    step "Installing PostgreSQL and build dependencies"
    apt-get install -y -q postgresql postgresql-contrib python3-dev libvirt-dev pkg-config

    step "Starting and enabling postgresql"
    systemctl enable postgresql
    systemctl start postgresql

    step "Setting up PostgreSQL user and database"
    # TODO: the "cos" Postgres role uses the hardcoded password "cos". This is
    # a known gap (tracked in COS_TODO.md) - move to a generated secret stored
    # under /opt/cos before this is used beyond a lab/dev network.
    sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='cos'" | grep -q 1 \
        || sudo -u postgres psql -c "CREATE USER cos WITH PASSWORD 'cos'"
    sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='cos'" | grep -q 1 \
        || sudo -u postgres psql -c "CREATE DATABASE cos OWNER cos"

    step "Running database migrations"
    COS_DATABASE_URL=postgresql+asyncpg://cos:cos@localhost/cos \
        /opt/cos/venv/bin/alembic --config "$REPO_DIR/alembic.ini" upgrade head

    step "Creating controller config directory"
    install -d -o cos -g cos -m 750 /opt/cos/config

    step "Generating admin API key (if needed)"
    if [[ ! -f /opt/cos/admin_api_key ]]; then
        python3.12 -c "import secrets; print(secrets.token_hex(32))" > /opt/cos/admin_api_key
        chown cos:cos /opt/cos/admin_api_key
        chmod 640 /opt/cos/admin_api_key
    else
        echo "  admin_api_key already exists, leaving it untouched."
    fi

    step "Installing Node.js 20 and nginx"
    NODE_OK=false
    if command -v node &>/dev/null; then
        NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)
        [[ "$NODE_VER" -ge 20 ]] && NODE_OK=true
    fi
    if [[ "$NODE_OK" == false ]]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        apt-get install -y nodejs
    fi
    apt-get install -y nginx

    step "Building the portal"
    cd "$REPO_DIR/portal"
    printf 'VITE_API_URL=\n' > .env.production
    if [[ -f package-lock.json ]]; then
        if ! npm ci --legacy-peer-deps; then
            echo "  npm ci failed, falling back to npm install --legacy-peer-deps"
            npm install --legacy-peer-deps
        fi
    else
        npm install --legacy-peer-deps
    fi
    npm run build
    [[ -d dist ]] || die "portal build did not produce a dist/ directory - old portal left in place"

    step "Deploying the portal (safe swap)"
    # rename() cannot replace a non-empty directory, so a plain "mv" of the new
    # build over an existing portal/ fails on a re-run. Swap via a .old
    # side-step instead, restoring it if the final move fails for any reason.
    rm -rf /opt/cos/portal.new
    cp -r dist /opt/cos/portal.new
    chown -R cos:cos /opt/cos/portal.new
    chmod -R 755 /opt/cos/portal.new
    chmod 755 /opt/cos
    if [[ -d /opt/cos/portal ]]; then
        rm -rf /opt/cos/portal.old
        mv /opt/cos/portal /opt/cos/portal.old
    fi
    if mv /opt/cos/portal.new /opt/cos/portal; then
        rm -rf /opt/cos/portal.old
    else
        if [[ -d /opt/cos/portal.old ]]; then
            mv /opt/cos/portal.old /opt/cos/portal
        fi
        die "failed to move the new portal build into place at /opt/cos/portal - previous portal has been restored (if it existed)"
    fi
    cd "$REPO_DIR"

    step "Installing nginx configuration"
    TS=$(date +%Y%m%d%H%M%S)
    NGINX_CONF_DEST=/etc/nginx/conf.d/websocket-upgrade.conf
    NGINX_SITE_DEST=/etc/nginx/sites-available/cos-portal
    BACKUP_CONF=""
    BACKUP_SITE=""
    if [[ -f "$NGINX_CONF_DEST" ]]; then
        BACKUP_CONF="${NGINX_CONF_DEST}.bak-${TS}"
        cp -p "$NGINX_CONF_DEST" "$BACKUP_CONF"
    fi
    if [[ -f "$NGINX_SITE_DEST" ]]; then
        BACKUP_SITE="${NGINX_SITE_DEST}.bak-${TS}"
        cp -p "$NGINX_SITE_DEST" "$BACKUP_SITE"
    fi
    cp "$REPO_DIR/nginx/websocket-upgrade.conf" "$NGINX_CONF_DEST"
    cp "$REPO_DIR/nginx/cos-portal.conf" "$NGINX_SITE_DEST"
    ln -sf "$NGINX_SITE_DEST" /etc/nginx/sites-enabled/cos-portal
    rm -f /etc/nginx/sites-enabled/default

    if ! nginx -t; then
        echo "  nginx -t failed - restoring previous configuration, NOT reloading nginx." >&2
        if [[ -n "$BACKUP_CONF" ]]; then cp -p "$BACKUP_CONF" "$NGINX_CONF_DEST"; else rm -f "$NGINX_CONF_DEST"; fi
        if [[ -n "$BACKUP_SITE" ]]; then cp -p "$BACKUP_SITE" "$NGINX_SITE_DEST"; else rm -f "$NGINX_SITE_DEST"; fi
        die "nginx configuration test failed - previous configuration restored"
    fi
    systemctl enable nginx >/dev/null 2>&1 || true
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
    else
        systemctl restart nginx
    fi

    step "Installing cos-controller systemd unit"
    UNIT_TMP=$(mktemp)
    cat > "$UNIT_TMP" <<'EOF'
[Unit]
Description=COS Controller
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=cos
Group=cos
WorkingDirectory=/opt/cos
Environment=COS_DATABASE_URL=postgresql+asyncpg://cos:cos@localhost/cos
EnvironmentFile=-/opt/cos/config/controller.env
ExecStart=/opt/cos/venv/bin/python -m controller.main
Restart=always
RestartSec=5
RuntimeDirectory=cos
RuntimeDirectoryMode=0750

[Install]
WantedBy=multi-user.target
EOF
    install_unit_if_changed /etc/systemd/system/cos-controller.service "$UNIT_TMP"
    rm -f "$UNIT_TMP"

    step "Restarting cos-controller"
    systemctl enable cos-controller >/dev/null 2>&1 || true
    systemctl restart cos-controller
    echo "  Waiting for the controller to answer on 127.0.0.1:8090..."
    CONTROLLER_READY=$(wait_for_controller)

    step "Verifying installation"
    OVERALL_OK=1

    if systemctl is-active --quiet cos-controller; then
        echo "  [OK] cos-controller service is active"
    else
        echo "  [FAIL] cos-controller service is NOT active"
        OVERALL_OK=0
    fi

    if systemctl is-active --quiet nginx; then
        echo "  [OK] nginx service is active"
    else
        echo "  [FAIL] nginx service is NOT active"
        OVERALL_OK=0
    fi

    ALEMBIC_CURRENT=$(COS_DATABASE_URL=postgresql+asyncpg://cos:cos@localhost/cos \
        /opt/cos/venv/bin/alembic --config "$REPO_DIR/alembic.ini" current 2>/dev/null | awk '{print $1}')
    ALEMBIC_HEAD=$(/opt/cos/venv/bin/alembic --config "$REPO_DIR/alembic.ini" heads 2>/dev/null | awk '{print $1}')
    if [[ -n "$ALEMBIC_CURRENT" && "$ALEMBIC_CURRENT" == "$ALEMBIC_HEAD" ]]; then
        echo "  [OK] database schema is at head ($ALEMBIC_CURRENT)"
    else
        echo "  [FAIL] database schema not at head (current: ${ALEMBIC_CURRENT:-none}, head: ${ALEMBIC_HEAD:-unknown})"
        OVERALL_OK=0
    fi

    if [[ "$CONTROLLER_READY" -eq 1 ]]; then
        echo "  [OK] controller answered health check within 30s"
    else
        echo "  [FAIL] controller did not answer health check within 30s"
        OVERALL_OK=0
    fi

    echo ""
    if [[ "$ADMIN_PASSWORD_EXISTED" -eq 1 ]]; then
        echo "  Admin password: unchanged (see /opt/cos/admin_password)"
    elif [[ -f /opt/cos/admin_password ]]; then
        echo "  Admin password: $(cat /opt/cos/admin_password)"
    else
        echo "  Admin password: not yet generated - check /opt/cos/admin_password shortly"
    fi
    if [[ "$ADMIN_KEY_EXISTED" -eq 1 ]]; then
        echo "  Admin API key: unchanged (see /opt/cos/admin_api_key)"
    else
        echo "  Admin API key: $(cat /opt/cos/admin_api_key)"
    fi
    echo "  Portal: http://$(hostname -I | awk '{print $1}')"

    if [[ "$OVERALL_OK" -eq 1 ]]; then
        echo ""
        echo "=== COS Controller installation: OK ==="
    else
        echo ""
        echo "=== COS Controller installation: FAIL (see [FAIL] lines above) ===" >&2
        exit 1
    fi

    if [[ "$RESTORE_MODE" -eq 1 ]]; then
        do_restore
    fi

fi

# ===========================================================================
# AGENT (steps 10-18)
# ===========================================================================

if [[ "$ROLE" == "agent" ]]; then

    AGENT_ENV_FILE=/opt/cos/config/agent.env

    step "Determining controller connection details"
    if [[ -f "$AGENT_ENV_FILE" ]]; then
        echo "  $AGENT_ENV_FILE already exists - keeping the existing controller URL/API key untouched."
    else
        if [[ -z "$CONTROLLER_URL" || -z "$CONTROLLER_API_KEY" ]]; then
            if [[ -t 0 ]]; then
                while [[ -z "$CONTROLLER_URL" ]]; do
                    read -r -p "Enter controller URL [http://CONTROLLER_IP:8090]: " CONTROLLER_URL
                    [[ -z "$CONTROLLER_URL" ]] && echo "Error: controller URL cannot be empty."
                done
                while [[ -z "$CONTROLLER_API_KEY" ]]; do
                    read -rs -p "Enter controller API key: " CONTROLLER_API_KEY
                    echo ""
                    [[ -z "$CONTROLLER_API_KEY" ]] && echo "Error: controller API key cannot be empty."
                done
            else
                die "no existing $AGENT_ENV_FILE and no --controller-url/--controller-api-key (or COS_INSTALL_CONTROLLER_URL/COS_INSTALL_CONTROLLER_API_KEY) given, and no interactive terminal available"
            fi
        fi
    fi

    step "Installing KVM/libvirt/OVS packages"
    apt-get install -y -q qemu-kvm libvirt-daemon-system libvirt-clients python3-libvirt cloud-image-utils openvswitch-switch libguestfs-tools python3-guestfs libguestfs-dev supermin build-essential pkg-config

    step "Adding cos to libvirt group, libvirt-qemu to cos group"
    usermod -aG libvirt cos
    if id libvirt-qemu > /dev/null 2>&1; then
        usermod -aG cos libvirt-qemu
    else
        echo "  libvirt-qemu user not found - skipping (may appear after libvirtd's first start)"
    fi

    step "Creating agent directories"
    install -d -o cos -g cos -m 750 /opt/cos/config
    # 755 so libvirt-qemu (which is not necessarily in the cos group at first
    # install) can traverse even without cos group membership.
    install -d -o cos -g cos -m 755 /var/lib/cos
    install -d -o cos -g cos -m 755 /var/lib/cos/images
    install -d -o cos -g cos -m 755 /var/lib/cos/vms
    install -d -o cos -g cos -m 755 /var/lib/cos/seeds

    step "Building libguestfs appliance for offline password reset"
    SUPERMIN_D="/usr/lib/x86_64-linux-gnu/guestfs/supermin.d"
    if [[ ! -d "$SUPERMIN_D" ]]; then
        echo "  warning: $SUPERMIN_D not found (libguestfs-tools package layout may differ); offline password reset will not work"
    else
        supermin --build --verbose --if-newer \
            --lock /var/lib/cos/guestfs-appliance.lock \
            --copy-kernel -f ext2 --host-cpu x86_64 \
            "$SUPERMIN_D" \
            -o /var/lib/cos/guestfs-appliance > /var/log/cos-supermin-build.log 2>&1
        if [[ $? -ne 0 || ! -f /var/lib/cos/guestfs-appliance/kernel ]]; then
            echo "  warning: supermin build failed; see /var/log/cos-supermin-build.log; offline password reset will not work"
        else
            # A fixed appliance needs README.fixed alongside kernel/initrd/root
            # (libguestfs checks for this marker file; supermin doesn't create it)
            echo "Fixed appliance for COS, built with supermin. Rebuild when libguestfs packages are upgraded." \
                > /var/lib/cos/guestfs-appliance/README.fixed
            chmod -R a+rX /var/lib/cos/guestfs-appliance
            echo "  libguestfs appliance built ($(du -sh /var/lib/cos/guestfs-appliance 2>/dev/null | cut -f1))"
        fi
    fi

    step "Adding cos to kvm group for libguestfs KVM acceleration"
    usermod -aG kvm cos || echo "  warning: failed to add cos to kvm group"

    step "Compiling password reset helper"
    HELPER_C="$REPO_DIR/scripts/cos-pw-reset-helper.c"
    HELPER_BIN="/usr/local/bin/cos-pw-reset-helper"
    if [[ ! -f "$HELPER_C" ]]; then
        die "password reset helper source not found at $HELPER_C"
    fi
    gcc -O2 -Wall -o "$HELPER_BIN" "$HELPER_C" -lguestfs || \
        die "failed to compile password reset helper"
    chmod 755 "$HELPER_BIN"
    echo "  helper compiled and installed at $HELPER_BIN"

    step "Generating node ID"
    if [[ ! -f /opt/cos/node_id ]]; then
        python3.12 -c "import uuid; print(str(uuid.uuid4()))" > /opt/cos/node_id
        chown cos:cos /opt/cos/node_id
        chmod 640 /opt/cos/node_id
    else
        echo "  node_id already exists, leaving it untouched."
    fi

    step "Writing agent configuration"
    if [[ -f "$AGENT_ENV_FILE" ]]; then
        echo "  $AGENT_ENV_FILE already exists - leaving it untouched."
    else
        cat > "$AGENT_ENV_FILE" <<EOF
COS_AGENT_CONTROLLER_URL=${CONTROLLER_URL}
COS_AGENT_CONTROLLER_API_KEY=${CONTROLLER_API_KEY}
COS_AGENT_LISTEN_PORT=8091
EOF
        chown cos:cos "$AGENT_ENV_FILE"
        chmod 640 "$AGENT_ENV_FILE"
    fi

    step "Installing cos-agent systemd unit"
    UNIT_TMP=$(mktemp)
    cat > "$UNIT_TMP" <<'EOF'
[Unit]
Description=COS Agent
After=network.target libvirtd.service
Requires=libvirtd.service

[Service]
Type=simple
User=cos
Group=cos
WorkingDirectory=/opt/cos
EnvironmentFile=-/opt/cos/config/agent.env
ExecStart=/opt/cos/venv/bin/python -m agent.main
Restart=always
RestartSec=5
RuntimeDirectory=cos
RuntimeDirectoryMode=0750

[Install]
WantedBy=multi-user.target
EOF
    install_unit_if_changed /etc/systemd/system/cos-agent.service "$UNIT_TMP"
    rm -f "$UNIT_TMP"

    step "Restarting cos-agent"
    systemctl enable cos-agent >/dev/null 2>&1 || true
    systemctl restart cos-agent
    sleep 3

    step "Verifying installation"
    OVERALL_OK=1

    if systemctl is-active --quiet cos-agent; then
        echo "  [OK] cos-agent service is active"
    else
        echo "  [FAIL] cos-agent service is NOT active"
        OVERALL_OK=0
    fi

    if ss -tln 2>/dev/null | awk '{print $4}' | grep -q ':8091$'; then
        echo "  [OK] WebSocket port 8091 is listening"
    else
        echo "  [FAIL] WebSocket port 8091 is not listening"
        OVERALL_OK=0
    fi

    echo ""
    echo "  Node ID: $(cat /opt/cos/node_id 2>/dev/null || echo unknown)"
    echo "  Controller URL: $(grep -oP '(?<=COS_AGENT_CONTROLLER_URL=).*' "$AGENT_ENV_FILE" 2>/dev/null || echo unknown)"

    if [[ "$OVERALL_OK" -eq 1 ]]; then
        echo ""
        echo "=== COS Agent installation: OK ==="
    else
        echo ""
        echo "=== COS Agent installation: FAIL (see [FAIL] lines above) ===" >&2
        exit 1
    fi

fi
