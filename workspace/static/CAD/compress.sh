#!/bin/bash
# compress.sh — Draco-compress every GLB in this folder.
#
# Run this after adding new CAD models. Re-running is idempotent —
# already-compressed files stay (near-)the same size. The viewer
# loads both compressed and uncompressed GLBs transparently, so a
# file that hasn't been through this script still works, just bigger.
#
# Only system requirement: nodejs. The gltf-transform tool itself
# is a local devDependency declared in package.json — the script
# installs it the first time you run, then runs fully offline
# thereafter.

set -e
cd "$(dirname "$0")"

# ── Check for nodejs ──────────────────────────────────────────────────
if ! command -v node >/dev/null 2>&1; then
    echo "error: nodejs not installed."
    echo "  Pi / Debian:"
    echo "    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -"
    echo "    sudo apt-get install -y nodejs"
    exit 1
fi

# ── Install the compressor locally if not already present ─────────────
LOCAL_BIN="./node_modules/.bin/gltf-transform"
if [ ! -x "$LOCAL_BIN" ]; then
    echo "first-time setup: installing gltf-transform (needs internet)…"
    npm install --silent --no-audit --no-fund
    echo "done. Future runs will use the local install, no internet needed."
    echo
fi

# ── Compress every GLB in this folder ─────────────────────────────────
total_before=0
total_after=0

shopt -s nullglob
for f in *.glb; do
    size_before=$(stat -c%s "$f")
    total_before=$((total_before + size_before))

    # ``optimize`` runs the recommended pipeline: dedup, prune, weld,
    # Draco compression. Keep full topology (no --simplify).
    "$LOCAL_BIN" optimize "$f" "$f.tmp" \
        --compress draco \
        2>/dev/null >/dev/null && mv "$f.tmp" "$f"

    size_after=$(stat -c%s "$f")
    total_after=$((total_after + size_after))

    pct=$(( (size_before - size_after) * 100 / size_before ))
    printf "  %-50s %5d K → %5d K  (-%d%%)\n" \
        "$f" $((size_before / 1024)) $((size_after / 1024)) "$pct"
done

echo
if [ "$total_before" -gt 0 ]; then
    printf "total: %d K → %d K  (-%d%%)\n" \
        $((total_before / 1024)) \
        $((total_after / 1024)) \
        $(( (total_before - total_after) * 100 / total_before ))
fi
