#!/bin/bash
# compress.sh — Draco-compress GLB files.
#
# Usage:
#   ./compress.sh                       compress this folder (workspace/static/CAD)
#   ./compress.sh DIR [DIR ...]         compress the given folder(s) instead
#
# Pass a project's CAD folder to compress its meshes, e.g.:
#   ./compress.sh ../../projects/apc/CAD
#   ./compress.sh /home/dorna/.../projects/apc/CAD
#
# Run this after adding new CAD models. Re-running is idempotent —
# already-compressed files stay (near-)the same size. The viewer loads
# both compressed and uncompressed GLBs transparently, so a file that
# hasn't been through this script still works, just bigger.
#
# Only system requirement: nodejs. The gltf-transform tool itself is a
# local devDependency declared in package.json (installed here, in this
# folder, on first run) — after that it runs fully offline.

set -e

# This folder — where node_modules / package.json live, regardless of
# which directories we end up compressing.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Check for nodejs ──────────────────────────────────────────────────
if ! command -v node >/dev/null 2>&1; then
    echo "error: nodejs not installed."
    echo "  Pi / Debian:"
    echo "    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -"
    echo "    sudo apt-get install -y nodejs"
    exit 1
fi

# ── Install the compressor locally if not already present ─────────────
LOCAL_BIN="$SCRIPT_DIR/node_modules/.bin/gltf-transform"
if [ ! -x "$LOCAL_BIN" ]; then
    echo "first-time setup: installing gltf-transform (needs internet)…"
    ( cd "$SCRIPT_DIR" && npm install --silent --no-audit --no-fund )
    echo "done. Future runs will use the local install, no internet needed."
    echo
fi

# ── Decide which folders to compress ──────────────────────────────────
# No args → this folder. Otherwise → the folder(s) given on the command
# line (so you can target a project's CAD/ without re-grinding the whole
# library here).
if [ "$#" -gt 0 ]; then
    TARGETS=("$@")
else
    TARGETS=("$SCRIPT_DIR")
fi

grand_before=0
grand_after=0

shopt -s nullglob
for dir in "${TARGETS[@]}"; do
    if [ ! -d "$dir" ]; then
        echo "skip: not a directory — $dir"
        continue
    fi
    abs_dir="$(cd "$dir" && pwd)"
    echo "compressing: $abs_dir"

    dir_before=0
    dir_after=0
    found=0
    for f in "$abs_dir"/*.glb; do
        found=1
        size_before=$(stat -c%s "$f")
        dir_before=$((dir_before + size_before))

        # ``optimize`` runs the recommended pipeline: dedup, prune, weld,
        # Draco compression. Keep full topology (no --simplify).
        "$LOCAL_BIN" optimize "$f" "$f.tmp" \
            --compress draco \
            2>/dev/null >/dev/null && mv "$f.tmp" "$f"

        size_after=$(stat -c%s "$f")
        dir_after=$((dir_after + size_after))

        pct=$(( (size_before - size_after) * 100 / size_before ))
        printf "  %-50s %5d K → %5d K  (-%d%%)\n" \
            "$(basename "$f")" $((size_before / 1024)) $((size_after / 1024)) "$pct"
    done

    if [ "$found" -eq 0 ]; then
        echo "  (no .glb files)"
    elif [ "$dir_before" -gt 0 ]; then
        printf "  subtotal: %d K → %d K  (-%d%%)\n" \
            $((dir_before / 1024)) $((dir_after / 1024)) \
            $(( (dir_before - dir_after) * 100 / dir_before ))
    fi
    echo

    grand_before=$((grand_before + dir_before))
    grand_after=$((grand_after + dir_after))
done

if [ "$grand_before" -gt 0 ]; then
    printf "total: %d K → %d K  (-%d%%)\n" \
        $((grand_before / 1024)) \
        $((grand_after / 1024)) \
        $(( (grand_before - grand_after) * 100 / grand_before ))
fi
