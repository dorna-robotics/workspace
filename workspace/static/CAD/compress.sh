#!/bin/bash
# compress.sh — Draco-compress every GLB in this folder.
#
# Run this after adding new CAD models. Re-running is idempotent —
# already-compressed files stay (near-)the same size. The viewer
# loads both compressed and uncompressed GLBs transparently, so a
# file that hasn't been through this script still works, just bigger.
#
# Compression is fully offline; only the one-time install of
# gltf-transform needs internet.

set -e

# ── One-time setup ────────────────────────────────────────────────────
# If gltf-transform isn't installed, do this once:
#
#   # Install nodejs (Pi / Debian — picks an LTS version):
#   curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
#   sudo apt-get install -y nodejs
#
#   # Install the compressor globally:
#   sudo npm install -g @gltf-transform/cli

if ! command -v gltf-transform >/dev/null 2>&1; then
    echo "error: gltf-transform not installed."
    echo "       sudo npm install -g @gltf-transform/cli"
    exit 1
fi

cd "$(dirname "$0")"

total_before=0
total_after=0

shopt -s nullglob
for f in *.glb; do
    size_before=$(stat -c%s "$f")
    total_before=$((total_before + size_before))

    # ``optimize`` runs the recommended pipeline: dedup, prune, weld,
    # Draco compression. ``--simplify`` is omitted — keep full topology.
    gltf-transform optimize "$f" "$f.tmp" \
        --compress draco \
        2>/dev/null >/dev/null && mv "$f.tmp" "$f"

    size_after=$(stat -c%s "$f")
    total_after=$((total_after + size_after))

    pct=$(( (size_before - size_after) * 100 / size_before ))
    printf "  %-50s %5d K → %5d K  (-%d%%)\n" \
        "$f" $((size_before / 1024)) $((size_after / 1024)) "$pct"
done

echo
printf "total: %d K → %d K  (-%d%%)\n" \
    $((total_before / 1024)) \
    $((total_after / 1024)) \
    $(( (total_before - total_after) * 100 / total_before ))
