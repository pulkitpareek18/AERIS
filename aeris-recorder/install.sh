#!/usr/bin/env bash
set -euo pipefail

AERIS_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AERIS_USER="${SUDO_USER:-${USER:-aeris}}"
AERIS_USER_HOME="$(getent passwd "$AERIS_USER" | cut -d: -f6)"
AERIS_USER_GROUP="$(id -gn "$AERIS_USER" 2>/dev/null || true)"

if [[ -z "$AERIS_USER_HOME" || -z "$AERIS_USER_GROUP" ]]; then
    echo "ERROR: Could not resolve the home directory for $AERIS_USER."
    exit 1
fi

AERIS_INSTALL_DIR="$AERIS_USER_HOME/aeris-recorder"
AERIS_CONFIG_FILE="$AERIS_INSTALL_DIR/aeris-config.json"
AERIS_DESKTOP_DIR="$AERIS_USER_HOME/Desktop"
AERIS_DATA_DIR="$AERIS_USER_HOME/aeris-data"
AERIS_SESSIONS_DIR="$AERIS_DATA_DIR/sessions"
AERIS_DB_FILE="$AERIS_DATA_DIR/aeris.db"

echo "===== AERIS RECORDER v3 INSTALLER ====="
echo "User: $AERIS_USER"
echo "Install directory: $AERIS_INSTALL_DIR"
echo "Data directory: $AERIS_DATA_DIR"

AERIS_MISSING_PACKAGES=()
for package in chromium tcpdump iw curl python3; do
    if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed"; then
        AERIS_MISSING_PACKAGES+=("$package")
    fi
done
if (( ${#AERIS_MISSING_PACKAGES[@]} > 0 )); then
    echo "Installing missing packages: ${AERIS_MISSING_PACKAGES[*]}"
    sudo apt-get update
    sudo apt-get install -y "${AERIS_MISSING_PACKAGES[@]}"
fi

mkdir -p "$AERIS_INSTALL_DIR" "$AERIS_SESSIONS_DIR" "$AERIS_DESKTOP_DIR"

# Install application files
if [[ "$AERIS_SOURCE_DIR" != "$AERIS_INSTALL_DIR" ]]; then
    install -m 0755 "$AERIS_SOURCE_DIR/aeris_db.py" "$AERIS_INSTALL_DIR/aeris_db.py"
    install -m 0755 "$AERIS_SOURCE_DIR/aeris_recorder_server.py" "$AERIS_INSTALL_DIR/aeris_recorder_server.py"
    install -m 0755 "$AERIS_SOURCE_DIR/aeris-recorder" "$AERIS_INSTALL_DIR/aeris-recorder"
    install -m 0644 "$AERIS_SOURCE_DIR/aeris-recorder.html" "$AERIS_INSTALL_DIR/aeris-recorder.html"
    install -m 0644 "$AERIS_SOURCE_DIR/aeris-icon.svg" "$AERIS_INSTALL_DIR/aeris-icon.svg"
else
    chmod 0755 "$AERIS_INSTALL_DIR/aeris_db.py" "$AERIS_INSTALL_DIR/aeris_recorder_server.py" "$AERIS_INSTALL_DIR/aeris-recorder"
    chmod 0644 "$AERIS_INSTALL_DIR/aeris-recorder.html" "$AERIS_INSTALL_DIR/aeris-icon.svg"
fi

# Configuration file handling: preserve existing, update database_path if missing
if [[ ! -f "$AERIS_CONFIG_FILE" ]]; then
    sed -e "s#/home/aeris#$AERIS_USER_HOME#g" \
        "$AERIS_SOURCE_DIR/aeris-config.example.json" > "$AERIS_CONFIG_FILE"
else
    echo "Preserving existing configuration: $AERIS_CONFIG_FILE"
    # Ensure database_path is in existing config if absent
    if ! grep -q "database_path" "$AERIS_CONFIG_FILE"; then
        python3 -c "
import json
p = '$AERIS_CONFIG_FILE'
with open(p, 'r') as f:
    cfg = json.load(f)
if 'database_path' not in cfg:
    cfg['database_path'] = '$AERIS_DB_FILE'
    with open(p, 'w') as f:
        json.dump(cfg, f, indent=2)
" || true
    fi
fi

# Set proper ownership on application files and database without altering root PCAPs
sudo chown -R "$AERIS_USER:$AERIS_USER_GROUP" "$AERIS_INSTALL_DIR"
sudo chown "$AERIS_USER:$AERIS_USER_GROUP" "$AERIS_DATA_DIR" "$AERIS_SESSIONS_DIR"
if [[ -f "$AERIS_DB_FILE" ]]; then
    sudo chown "$AERIS_USER:$AERIS_USER_GROUP" "$AERIS_DB_FILE"* 2>/dev/null || true
fi

# Run SQLite database schema initialization / migrations
echo "Initializing / migrating AERIS database..."
sudo -u "$AERIS_USER" python3 "$AERIS_INSTALL_DIR/aeris_recorder_server.py" --init-db

sudo ln -sfn "$AERIS_INSTALL_DIR/aeris-recorder" /usr/local/bin/aeris-recorder

AERIS_DESKTOP_FILE="$AERIS_DESKTOP_DIR/AERIS-Recorder.desktop"
cat > "$AERIS_DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=AERIS Recorder
Comment=Record labeled Wi-Fi CSI experiments
Exec=/usr/local/bin/aeris-recorder
Icon=$AERIS_INSTALL_DIR/aeris-icon.svg
Terminal=true
Categories=Science;Utility;
StartupNotify=true
EOF
chmod 0755 "$AERIS_DESKTOP_FILE"
gio set "$AERIS_DESKTOP_FILE" metadata::trusted true >/dev/null 2>&1 || true

sudo install -m 0644 "$AERIS_DESKTOP_FILE" /usr/share/applications/aeris-recorder.desktop

PI_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || echo "aeris.local")"

echo
echo "===== INSTALLATION COMPLETE (v3.0.0) ====="
echo "AERIS Recorder v3 is ready."
echo
echo "Access URLs:"
echo "  • Raspberry Pi Touchscreen : http://127.0.0.1:8765"
echo "  • Remote Computer / Laptop : http://aeris.local:8765  (or http://${PI_IP}:8765)"
echo
echo "Double-click the AERIS Recorder icon on the desktop to launch."
