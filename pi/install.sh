#!/usr/bin/env bash
# Installerer bf-analyzer som systemd-tjenester paa en Raspberry Pi (eller annen Linux).
# Idempotent: trygt aa kjoere paa nytt. Kjoer som vanlig bruker (sudo brukes kun for systemd).
set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
    echo "Feil: dette skriptet er for Linux (Raspberry Pi OS)." >&2
    exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
    echo "Feil: kjoer som vanlig bruker, ikke root. sudo brukes automatisk der det trengs." >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "Feil: python3 mangler. Installer med: sudo apt install python3" >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)'; then
    echo "Feil: python3 maa vaere 3.7 eller nyere (fant $(python3 -V 2>&1))." >&2
    exit 1
fi
if ! python3 -c 'import sqlite3, sys; sys.exit(0 if tuple(int(x) for x in sqlite3.sqlite_version.split(".")[:2]) >= (3, 24) else 1)'; then
    echo "Feil: SQLite maa vaere 3.24 eller nyere (trengs for UPSERT)." >&2
    echo "Tips: sudo apt update && sudo apt full-upgrade, eller bruk nyere Raspberry Pi OS." >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "Feil: systemd (systemctl) mangler." >&2
    exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
mkdir -p "$DIR/outputs"
echo "Installerer fra $DIR som bruker $RUN_USER"

# Med nginx: appen lytter bare paa 127.0.0.1, og nginx viser den paa http://<pi>/bfpris/
# (kun lokalt nett), ved siden av andre nettsider. Uten nginx: direkte paa port 8080.
if [ -d /etc/nginx/sites-enabled ] && systemctl is-active --quiet nginx; then
    WEB_HOST=127.0.0.1
else
    WEB_HOST=0.0.0.0
fi

UNITS="bf-analyzer-web.service bf-analyzer-collect.service bf-analyzer-collect.timer"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for u in $UNITS; do
    sed -e "s|@USER@|$RUN_USER|g" -e "s|@DIR@|$DIR|g" -e "s|@HOST@|$WEB_HOST|g" "$DIR/pi/$u" > "$TMP/$u"
    sudo install -m 644 "$TMP/$u" "/etc/systemd/system/$u"
done

sudo systemctl daemon-reload
sudo systemctl enable bf-analyzer-web.service bf-analyzer-collect.timer
sudo systemctl restart bf-analyzer-web.service
sudo systemctl start bf-analyzer-collect.timer

HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ "$WEB_HOST" = 127.0.0.1 ]; then
    NAMES="$(hostname) $(hostname).local $HOST"
    sed -e "s|@NAMES@|$NAMES|g" "$DIR/pi/nginx-bfpris.conf" > "$TMP/nginx"
    SITE=/etc/nginx/sites-available/bf-analyzer
    OLD="$TMP/nginx.old"
    [ -f "$SITE" ] && cp "$SITE" "$OLD"
    sudo install -m 644 "$TMP/nginx" "$SITE"
    sudo ln -sf "$SITE" /etc/nginx/sites-enabled/bf-analyzer
    if sudo nginx -t >/dev/null 2>&1; then
        sudo systemctl reload nginx
        URL="http://$(hostname).local/bfpris/"
    else
        echo "Advarsel: nginx godtok ikke oppsettet, tar det bort igjen. Se: sudo nginx -t" >&2
        if [ -f "$OLD" ]; then sudo install -m 644 "$OLD" "$SITE"; else sudo rm -f "$SITE" /etc/nginx/sites-enabled/bf-analyzer; fi
        URL="(nginx-feil, se over)"
    fi
else
    URL="http://${HOST:-<pi-adresse>}:8080/bfpris/"
fi
echo
echo "Ferdig. Aapne i nettleseren:  $URL"
echo "Neste planlagte innsamling:   systemctl list-timers bf-analyzer-collect.timer"
echo "Kjor en innsamling na:        sudo systemctl start bf-analyzer-collect.service"
echo "Logg:                         journalctl -u bf-analyzer-collect -f"
