#!/bin/bash
# Back up the HubProfit SQLite volume before a redeploy.
#
# Takes a CONSISTENT snapshot: the container is stopped first, because
# SQLite writes are not atomic at the file level and copying a live
# database can capture a half-written page. The container restarts
# afterwards either way.
#
# Safe to run repeatedly - each run writes a new timestamped archive.

set -uo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/hubprofit-backups}"
STAMP=$(date +%Y-%m-%d_%H%M%S)
ARCHIVE="hubprofit_data_${STAMP}.tar.gz"

fail() { echo; echo "FAILED: $*"; echo "Do NOT redeploy."; exit 1; }

echo "=============================================="
echo " HubProfit volume backup"
echo "=============================================="

# ---- 1. Locate the volume -------------------------------------------
mapfile -t VOLS < <(docker volume ls -q | grep -i 'hubprofit_data' || true)
if [ "${#VOLS[@]}" -eq 0 ]; then
    echo "No volume matching 'hubprofit_data'. All volumes:"
    docker volume ls
    fail "could not find the HubProfit volume"
fi
if [ "${#VOLS[@]}" -gt 1 ]; then
    echo "More than one candidate volume:"
    printf '  %s\n' "${VOLS[@]}"
    fail "ambiguous volume name - tell Claude which one is live"
fi
VOL="${VOLS[0]}"
echo "Volume    : $VOL"

# ---- 2. Locate the container ----------------------------------------
CON=$(docker ps -a --filter volume="$VOL" --format '{{.Names}}' | head -1)
[ -z "$CON" ] && CON=$(docker ps -a --format '{{.Names}}' | grep -i hubprofit | head -1)
if [ -z "$CON" ]; then
    fail "no container is using $VOL"
fi
RUNNING=$(docker inspect -f '{{.State.Running}}' "$CON" 2>/dev/null)
echo "Container : $CON (running=$RUNNING)"

# ---- 3. Record what is in there now ----------------------------------
echo
echo "--- current contents ---"
docker run --rm -v "$VOL":/data:ro python:3.12-slim python - <<'PY' || echo "(could not read DB - continuing, the tar is what matters)"
import os, sqlite3
p = "/data/hub.db"
if not os.path.exists(p):
    print("hub.db NOT FOUND in the volume")
else:
    print("hub.db size : %.1f KB" % (os.path.getsize(p) / 1024))
    c = sqlite3.connect(p)
    print("user_version:", c.execute("PRAGMA user_version").fetchone()[0],
          "(0 or 1 = pre-upgrade, 3 = upgraded)")
    for t in ("daily_entries", "drivers", "businesses"):
        try:
            n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"{t:<13}: {n}")
        except sqlite3.OperationalError:
            print(f"{t:<13}: (table does not exist yet)")
    try:
        lo, hi = c.execute(
            "SELECT MIN(date), MAX(date) FROM daily_entries").fetchone()
        print("date range  :", lo, "->", hi)
    except sqlite3.OperationalError:
        pass
    c.close()
PY

# ---- 4. Stop, snapshot, restart -------------------------------------
mkdir -p "$BACKUP_DIR"
echo
if [ "$RUNNING" = "true" ]; then
    echo "Stopping $CON for a consistent copy..."
    docker stop "$CON" >/dev/null || fail "could not stop $CON"
fi

echo "Writing $BACKUP_DIR/$ARCHIVE ..."
docker run --rm \
    -v "$VOL":/data:ro \
    -v "$BACKUP_DIR":/backup \
    alpine tar czf "/backup/$ARCHIVE" -C /data . \
    || { [ "$RUNNING" = "true" ] && docker start "$CON" >/dev/null
         fail "the tar step errored"; }

if [ "$RUNNING" = "true" ]; then
    echo "Restarting $CON ..."
    docker start "$CON" >/dev/null || echo "WARNING: could not restart $CON"
fi

# ---- 5. Verify the archive ------------------------------------------
echo
echo "--- verifying the archive ---"
[ -s "$BACKUP_DIR/$ARCHIVE" ] || fail "archive is missing or empty"

LISTING=$(docker run --rm -v "$BACKUP_DIR":/backup alpine \
          tar tzvf "/backup/$ARCHIVE" 2>&1) || fail "archive will not read back"
echo "$LISTING"

echo "$LISTING" | grep -q 'hub.db' || fail "archive does not contain hub.db"
DBBYTES=$(echo "$LISTING" | awk '/hub\.db$/ {print $3; exit}')
[ "${DBBYTES:-0}" -gt 0 ] 2>/dev/null || fail "hub.db in the archive is 0 bytes"

echo
echo "=============================================="
echo " BACKUP OK - safe to redeploy"
echo "=============================================="
echo "File    : $BACKUP_DIR/$ARCHIVE"
echo "hub.db  : $DBBYTES bytes"
ls -lh "$BACKUP_DIR"
echo
echo "To restore later (only if something goes wrong):"
echo "  docker stop $CON"
echo "  docker run --rm -v $VOL:/data -v $BACKUP_DIR:/backup alpine \\"
echo "    sh -c 'rm -rf /data/* && tar xzf /backup/$ARCHIVE -C /data'"
echo "  docker start $CON"
