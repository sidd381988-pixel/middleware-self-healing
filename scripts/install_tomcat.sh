#!/usr/bin/env bash
# Install Apache Tomcat 10 from scratch on RHEL 8/9
# Run as root: sudo bash scripts/install_tomcat.sh

set -euo pipefail

TOMCAT_VERSION="10.1.40"
TOMCAT_USER="tomcat"
TOMCAT_GROUP="tomcat"
TOMCAT_HOME="/opt/tomcat"
TOMCAT_PORT=8080
JAVA_PKG="java-11-openjdk-devel"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ── 1. Install Java ────────────────────────────────────────────────────────────
log "Installing $JAVA_PKG ..."
dnf install -y "$JAVA_PKG"

JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")
log "JAVA_HOME = $JAVA_HOME"

# ── 2. Create dedicated tomcat user ───────────────────────────────────────────
if ! id "$TOMCAT_USER" &>/dev/null; then
    log "Creating system user: $TOMCAT_USER"
    groupadd --system "$TOMCAT_GROUP"
    useradd --system --gid "$TOMCAT_GROUP" --home-dir "$TOMCAT_HOME" \
            --no-create-home --shell /sbin/nologin "$TOMCAT_USER"
fi

# ── 3. Download and extract Tomcat ─────────────────────────────────────────────
TOMCAT_MAJOR="${TOMCAT_VERSION%%.*}"
MIRROR_URL="https://downloads.apache.org/tomcat/tomcat-${TOMCAT_MAJOR}/v${TOMCAT_VERSION}/bin/apache-tomcat-${TOMCAT_VERSION}.tar.gz"
ARCHIVE_URL="https://archive.apache.org/dist/tomcat/tomcat-${TOMCAT_MAJOR}/v${TOMCAT_VERSION}/bin/apache-tomcat-${TOMCAT_VERSION}.tar.gz"
TMP_TAR="/tmp/apache-tomcat-${TOMCAT_VERSION}.tar.gz"

log "Downloading Tomcat $TOMCAT_VERSION ..."
if ! curl -fsSL "$MIRROR_URL" -o "$TMP_TAR" 2>/dev/null; then
    log "Main mirror returned 404 — falling back to Apache archive ..."
    curl -fsSL "$ARCHIVE_URL" -o "$TMP_TAR"
fi

log "Extracting to $TOMCAT_HOME ..."
mkdir -p "$TOMCAT_HOME"
tar xzf "$TMP_TAR" -C "$TOMCAT_HOME" --strip-components=1
rm -f "$TMP_TAR"

# ── 4. Set permissions ─────────────────────────────────────────────────────────
log "Setting ownership and permissions ..."
chown -R "$TOMCAT_USER:$TOMCAT_GROUP" "$TOMCAT_HOME"
chmod -R u=rwX,g=rX,o= "$TOMCAT_HOME"
chmod +x "$TOMCAT_HOME"/bin/*.sh

# ── 5. Create setenv.sh with initial heap ─────────────────────────────────────
SETENV="$TOMCAT_HOME/bin/setenv.sh"
if [ ! -f "$SETENV" ]; then
    log "Creating $SETENV ..."
    cat > "$SETENV" <<'SETENV_EOF'
export CATALINA_OPTS="-Xms512m -Xmx512m -XX:+UseG1GC -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/var/lib/middleware-agent/dumps"
SETENV_EOF
    chown "$TOMCAT_USER:$TOMCAT_GROUP" "$SETENV"
    chmod 750 "$SETENV"
fi

# ── 6. Create systemd service unit ────────────────────────────────────────────
log "Creating systemd service ..."
cat > /etc/systemd/system/tomcat.service <<UNIT_EOF
[Unit]
Description=Apache Tomcat Web Application Server
After=network.target

[Service]
Type=forking
User=$TOMCAT_USER
Group=$TOMCAT_GROUP

Environment="JAVA_HOME=$JAVA_HOME"
Environment="CATALINA_PID=$TOMCAT_HOME/temp/tomcat.pid"
Environment="CATALINA_HOME=$TOMCAT_HOME"
Environment="CATALINA_BASE=$TOMCAT_HOME"

ExecStart=$TOMCAT_HOME/bin/startup.sh
ExecStop=$TOMCAT_HOME/bin/shutdown.sh

Restart=on-failure
RestartSec=10

StandardOutput=append:$TOMCAT_HOME/logs/catalina.out
StandardError=append:$TOMCAT_HOME/logs/catalina.out

[Install]
WantedBy=multi-user.target
UNIT_EOF

systemctl daemon-reload
systemctl enable tomcat

# ── 7. Open firewall port ─────────────────────────────────────────────────────
if command -v firewall-cmd &>/dev/null; then
    log "Opening firewall port $TOMCAT_PORT/tcp ..."
    firewall-cmd --permanent --add-port="${TOMCAT_PORT}/tcp"
    firewall-cmd --reload
fi

# ── 8. SELinux: allow Tomcat to bind its port ─────────────────────────────────
if command -v semanage &>/dev/null; then
    log "Configuring SELinux for port $TOMCAT_PORT ..."
    semanage port -a -t http_port_t -p tcp "$TOMCAT_PORT" 2>/dev/null || \
    semanage port -m -t http_port_t -p tcp "$TOMCAT_PORT"
fi

# ── 9. Create agent state directory ──────────────────────────────────────────
log "Creating agent state directory ..."
mkdir -p /var/lib/middleware-agent/dumps
chown -R "$TOMCAT_USER:$TOMCAT_GROUP" /var/lib/middleware-agent

# ── 10. Start Tomcat ─────────────────────────────────────────────────────────
log "Starting Tomcat ..."
systemctl start tomcat

sleep 5
if systemctl is-active --quiet tomcat; then
    log "Tomcat is running.  Check http://$(hostname -I | awk '{print $1}'):${TOMCAT_PORT}/"
else
    log "ERROR: Tomcat failed to start.  Check: journalctl -u tomcat -n 50"
    exit 1
fi

log "Installation complete."
