#!/usr/bin/env bash
set -e

# ---------------------------------------------------------------------------
# COS - Cloud Operating System installer
# Usage: cos-install.sh --role controller|agent
# ---------------------------------------------------------------------------

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
ROLE=""

usage() {
    echo "Usage: $0 --role controller|agent"
    exit 1
}

# --- Argument parsing -------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --role)
            ROLE="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

if [[ "$ROLE" != "controller" && "$ROLE" != "agent" ]]; then
    usage
fi

# --- Root check -------------------------------------------------------------

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Error: this script must be run as root."
    exit 1
fi

# --- OS check ---------------------------------------------------------------

if ! grep -q 'Ubuntu 24.04' /etc/os-release 2>/dev/null; then
    echo "Error: this script requires Ubuntu 24.04 LTS."
    exit 1
fi

# ===========================================================================
# COMMON
# ===========================================================================

echo "[1/N] Installing common system packages..."
apt-get update -q
apt-get install -y -q python3.12 python3.12-venv python3.12-dev git curl wget pkg-config libvirt-dev build-essential

echo "[2/N] Creating cos group and user..."
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

SUDO_USER=${SUDO_USER:-$USER}
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
    usermod -aG cos "$SUDO_USER"
fi

echo "[3/N] Creating directories..."
install -d -o cos -g cos -m 750 /opt/cos
install -d -o cos -g cos -m 750 /opt/cos/venv
install -d -o cos -g cos -m 750 /var/log/cos
install -d -o cos -g cos -m 750 /run/cos

echo "[4/N] Creating Python virtual environment..."
python3.12 -m venv /opt/cos/venv
chown -R cos:cos /opt/cos/venv

echo "[5/N] Installing COS Python package..."
cd "$REPO_DIR" && git pull
/opt/cos/venv/bin/pip install "$REPO_DIR/" -q
/opt/cos/venv/bin/pip install -r "$REPO_DIR/requirements.txt" -q

# ===========================================================================
# CONTROLLER
# ===========================================================================

if [[ "$ROLE" == "controller" ]]; then

    echo "[C1] Installing PostgreSQL and build dependencies..."
    apt-get install -y -q postgresql postgresql-contrib python3-dev libvirt-dev pkg-config

    echo "[C2] Starting and enabling postgresql..."
    systemctl enable postgresql
    systemctl start postgresql

    echo "[C3] Setting up PostgreSQL user and database..."
    su -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='cos'\" | grep -q 1 || \
           psql -c \"CREATE USER cos WITH PASSWORD 'cos'\"" postgres
    su -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='cos'\" | grep -q 1 || \
           psql -c \"CREATE DATABASE cos OWNER cos\"" postgres

    echo "[C4] Running database migrations..."
    COS_DATABASE_URL=postgresql+asyncpg://cos:cos@localhost/cos \
        /opt/cos/venv/bin/alembic --config "$REPO_DIR/alembic.ini" upgrade head

    echo "[C5] Creating controller config directory..."
    install -d -o cos -g cos -m 750 /opt/cos/config

    echo "[C6] Generating admin API key..."
    if [[ ! -f /opt/cos/admin_api_key ]]; then
        python3.12 -c "import secrets; print(secrets.token_hex(32))" > /opt/cos/admin_api_key
        chown cos:cos /opt/cos/admin_api_key
        chmod 640 /opt/cos/admin_api_key
    else
        echo "       admin_api_key already exists, skipping."
    fi

    echo "[C-PORTAL] Installing Node.js 20 and nginx..."
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

    echo "[C-PORTAL] Building portal..."
    SERVER_IP=$(hostname -I | awk '{print $1}')
    cd "$REPO_DIR/portal"
    printf 'VITE_API_URL=\n' > .env.production
    npm install --legacy-peer-deps
    npm install react-is --legacy-peer-deps
    npm run build
    rm -rf /opt/cos/portal
    cp -r dist/ /opt/cos/portal
    chown -R cos:cos /opt/cos/portal
    chmod 755 /opt/cos
    chmod -R 755 /opt/cos/portal

    echo "[C-PORTAL] Installing nginx config..."
    cp "$REPO_DIR/nginx/cos-portal.conf" /etc/nginx/sites-available/cos-portal
    sed -i "s|root /opt/cos/portal;|root /opt/cos/portal;|" /etc/nginx/sites-available/cos-portal
    sed -i "s|proxy_pass http://127.0.0.1:8090;|proxy_pass http://${SERVER_IP}:8090;|" /etc/nginx/sites-available/cos-portal
    ln -sf /etc/nginx/sites-available/cos-portal /etc/nginx/sites-enabled/cos-portal
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl enable nginx && systemctl restart nginx

    echo "[C7] Writing cos-controller systemd service..."
    cat > /etc/systemd/system/cos-controller.service <<'EOF'
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

    echo "[C8] Enabling and starting cos-controller..."
    rm -f /opt/cos/admin_password
    systemctl daemon-reload
    systemctl enable cos-controller
    systemctl restart cos-controller
    sleep 3

    echo ""
    echo "COS Controller installed."
    echo "  Admin API key:  $(cat /opt/cos/admin_api_key)"
    echo "  Admin password: $(cat /opt/cos/admin_password)"
    echo "COS Portal available at http://$(hostname -I | awk '{print $1}')"

fi

# ===========================================================================
# AGENT
# ===========================================================================

if [[ "$ROLE" == "agent" ]]; then

    echo "[A-PROMPT] Collecting controller details..."
    CONTROLLER_URL=""
    CONTROLLER_API_KEY=""

    while [[ -z "$CONTROLLER_URL" ]]; do
        read -p "Enter controller URL [http://CONTROLLER_IP:8090]: " CONTROLLER_URL
        if [[ -z "$CONTROLLER_URL" ]]; then
            echo "Error: controller URL cannot be empty."
        fi
    done

    while [[ -z "$CONTROLLER_API_KEY" ]]; do
        read -sp "Enter controller API key: " CONTROLLER_API_KEY
        echo ""
        if [[ -z "$CONTROLLER_API_KEY" ]]; then
            echo "Error: controller API key cannot be empty."
        fi
    done

    echo "[A1] Installing KVM/libvirt packages..."
    apt-get install -y -q qemu-kvm libvirt-daemon-system libvirt-clients python3-libvirt

    echo "[A2] Adding cos user to libvirt group..."
    usermod -aG libvirt cos

    echo "[A3] Creating agent directories..."
    install -d -o cos -g cos -m 750 /opt/cos/config
    install -d -o cos -g cos -m 750 /var/lib/cos
    install -d -o cos -g cos -m 750 /var/lib/cos/images

    echo "[A4] Generating node ID..."
    if [[ ! -f /opt/cos/node_id ]]; then
        python3.12 -c "import uuid; print(str(uuid.uuid4()))" > /opt/cos/node_id
        chown cos:cos /opt/cos/node_id
        chmod 640 /opt/cos/node_id
    else
        echo "       node_id already exists, skipping."
    fi

    echo "[A4b] Writing agent config..."
    cat > /opt/cos/config/agent.env <<EOF
COS_AGENT_CONTROLLER_URL=${CONTROLLER_URL}
COS_AGENT_CONTROLLER_API_KEY=${CONTROLLER_API_KEY}
COS_AGENT_LISTEN_PORT=8091
EOF
    chown cos:cos /opt/cos/config/agent.env
    chmod 640 /opt/cos/config/agent.env

    echo "[A5] Writing cos-agent systemd service..."
    cat > /etc/systemd/system/cos-agent.service <<'EOF'
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

    echo "[A6] Starting cos-agent..."
    systemctl daemon-reload
    systemctl enable cos-agent
    systemctl start cos-agent

    echo ""
    echo "COS Agent installed and started."
    echo "  Node ID: $(cat /opt/cos/node_id)"
    echo "  Controller URL: ${CONTROLLER_URL}"

fi
