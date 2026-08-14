#!/bin/sh
# Back up the HubProfit database before upgrading.
#
# Stops the container, copies /data/hub.db out, starts it again. Stopping
# first matters: SQLite writes are not atomic at the file level, so copying
# a running database can capture a half-written page. The container has to
# restart for the upgrade anyway, so this costs nothing.
#
# Usage:   ./backup-hubprofit.sh [container-name]
# Default container name is hubprofit-hubprofit-1 (Portainer's naming).

set -e

CON="${1:-hubprofit-hubprofit-1}"
OUT="$HOME/hub-backup-$(date +%F_%H%M).db"

if ! docker inspect "$CON" >/dev/null 2>&1; then
    echo "No container named '$CON'."
    echo "Your containers:"
    docker ps -a --format '  {{.Names}}'
    echo
    echo "Re-run with the right name:  $0 <container-name>"
    exit 1
fi

echo "Container : $CON"
echo "Backup to : $OUT"
echo

docker stop "$CON"
docker cp "$CON":/data/hub.db "$OUT"
docker start "$CON"

echo
if [ -s "$OUT" ]; then
    ls -lh "$OUT"
    echo
    echo "BACKUP OK - safe to upgrade."
    echo
    echo "To restore:"
    echo "  docker stop $CON"
    echo "  docker cp $OUT $CON:/data/hub.db"
    echo "  docker start $CON"
else
    echo "FAILED: $OUT is missing or empty. Do NOT upgrade."
    exit 1
fi
